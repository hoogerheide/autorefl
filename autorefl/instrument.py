"""Reflectometry instrument definitions"""

from pathlib import Path
import numpy as np
import json
import warnings
from reflred.candor import edges
from reflred.resolution import divergence

CALIBRATION_PATH = Path(__file__).parent / 'calibration'

def q2a(q, L):
    return np.degrees(np.arcsin(np.array(q)*L/(4*np.pi)))

def a2q(T, L):
    return 4*np.pi/L * np.sin(np.radians(T))

class ReflectometerBase(object):
    """Base reflectometer"""
    def __init__(self) -> None:
        self._L = None
        self._dL = None
        self.xlabel = ''
        self.name = None
        self.resolution = 'normal'

        # assumes that the detector arm motion is the slowest component
        self.topspeed = 1.0 # units of degrees / second for detector arm
        self.basespeed = 0.2 # units of degrees / second for detector arm
        self.acceleration = 0.5 # units of degrees / second^2 for detector arm
        self.x = None   # current position

        # instrument geometry
        self._L12 = None
        self._L2S = None
        self._LS3 = None
        self._L34 = None
        self.footprint = None
        self.sample_width = None
        self._S3Offset = 0.0
        self._R12 = 1.0

        # default t(Q) scaling parameters. Here t(Q) \propto _mon0 + _mon1 * Q^Qpow
        self._mon0 = 0.0
        self._mon1 = 1.0
        self._Qpow = 2.0

    def x2q(self, x):
        pass

    def x2a(self, x):
        pass

    def qrange2xrange(self, qmin, qmax):
        pass

    def intensity(self, x):
        pass

    def meastime(self, x, totaltime):

        q = self.x2q(np.array(x))

        f = self._mon0 + self._mon1 * q ** self._Qpow

        return totaltime * f / sum(f)

    def T(self, x):
        
        return self.x2a(x)

    def dT(self, x):
        usesample = True if self.footprint > self.sample_width else False 
        return divergence(self.get_slits(x), self.get_slit_distances(), T=self.T(x), sample_width=self.sample_width, use_sample=usesample)

    def L(self, x):
        
        return np.array(np.ones_like(x) * self._L, ndmin=1)

    def dL(self, x):
        
        return np.array(np.ones_like(x) * self._dL, ndmin=1)

    def Q2TdTLdL(self, qs, measx, measQ):
        """
        Converts a Q value into T, dT, L, dL variables.
        Replaces gen_new_variables. Used for calculating R(Q) profiles
        Logic derived from reflred.candor._rebin_bank

        Inputs:
        qs -- Q values to convert to variables
        measx -- possible x values
        measQ -- possible Q bins

        Returns:
        T -- average angle over all measx, one for each value of qs
        dT -- average angular divergence
        L -- average wavelength
        dL -- average wavelength spread
        """

        # calculate all variables
        _Q = self.x2q(measx)
        _T = self.T(measx)
        _dT = self.dT(measx)
        _L = self.L(measx)
        _dL = self.dL(measx)

        # calculate q bin edges
        q_edges = edges(measQ, extended=True)
        nbins = len(q_edges) - 1

        # calculate flattened bin index
        bin_index = np.searchsorted(q_edges, _Q).ravel() - 1

        # calculate normalization factor
        sum_w = np.bincount(bin_index, minlength=nbins)
        sum_w += (sum_w == 0)  # protect against divide by zero

        # Combine wavelengths
        sum_L = np.bincount(bin_index, weights=_L.ravel(), minlength=nbins)
        sum_dLsq = np.bincount(bin_index, weights=(_dL.ravel()**2+_L.ravel()**2), minlength=nbins)
        bar_L = sum_L/sum_w
        bar_dL = np.sqrt(sum_dLsq/sum_w - (sum_L/sum_w)**2)

        # Combine angles
        sum_T = np.bincount(bin_index, weights=_T.ravel(), minlength=nbins)
        sum_dT = np.bincount(bin_index, weights=_dT.ravel()**2, minlength=nbins)
        bar_T = sum_T/sum_w
        bar_dT = np.sqrt(sum_dT/sum_w)

        # find indices corresponding to requested values and return results
        idxs = np.searchsorted(measQ, qs) + 1
        #res = [(bar_T[idx], bar_dT[idx], bar_L[idx], bar_dL[idx]) for idx in idxs]

        return (bar_T[idxs], bar_dT[idxs], bar_L[idxs], bar_dL[idxs])


    def get_slits(self, x):
        x = np.array(x, ndmin=1)
        sintheta = np.sin(np.radians(self.x2a(x)))
        s2 = self.footprint * sintheta / ((self._R12 + 1) * self._L2S / self._L12 + 1)
        s1 = self._R12 * s2
        s3 = (s1 + s2) * (self._L2S + self._LS3) / self._L12 + s2 + self._S3Offset
        s4 = (s1 + s2) * (self._L2S + self._LS3 + self._L34) / self._L12 + s2 + self._S3Offset

        return s1, s2, s3, s4

    def get_slit_distances(self):

        return -(self._L12 + self._L2S), -self._L2S, self._LS3, self._LS3 + self._L34

    def movetime(self, x):

        if self.x is None:
            return np.array([0])
        else:
            x = np.array(x, ndmin=1)

            # convert x to angle units
            newT = self.x2a(x)
            curT = self.x2a(self.x)

            # detector arm motion is 2 * dTheta
            dx = 2 * np.abs(newT - curT)

            t = np.empty_like(dx)

            # total time that arm is accelerating
            accel_t = (self.topspeed - self.basespeed) / self.acceleration

            # total distance that can be traversed in one acceleration / deceleration cycle without achieving top speed
            max_accel_dx = 2 * (0.5 * self.acceleration * accel_t ** 2 + self.basespeed * accel_t)

            # select points in the acceleration only regime
            accel_crit = (dx < max_accel_dx)

            # top velocity reached
            t[~accel_crit] = 2 * accel_t + (dx[~accel_crit] - max_accel_dx) / self.topspeed

            # top velocity not reached
            t[accel_crit] = 2 * self.basespeed / self.acceleration * (-1 + np.sqrt(1 + 2 * (dx[accel_crit] / 2) * self.acceleration / self.basespeed ** 2))

            return t

class MAGIK(ReflectometerBase):
    """ MAGIK Reflectometer
    x = Q """
    def __init__(self) -> None:
        super().__init__()
        self._L = np.array([5.0])
        self._dL = 0.01648374 * self._L / 2.355
        self.xlabel = r'$Q_z$ (' + u'\u212b' + r'$^{-1}$)'
        self.name = 'MAGIK'
        self.resolution = 'normal'
        self.topspeed = 1.0
        self.basespeed = 0.2
        self.acceleration = 0.5
        # As of 1/24/2022:
        # Base: 0.2 deg / sec
        # Acceleration: 0.5 deg / sec^2
        # Top velocity: 1.0 deg / sec

        # instrument geometry
        self._L12 = 1403.
        self._L2S = 330.
        self._LS3 = 229.
        self._L34 = 939.
        self.footprint = 45.
        self._S3Offset = 1.22
        self._R12 = 1.0
        self.sample_width = np.inf

        # best practice Q scaling
        self._mon0 = 30.0
        self._mon1 = 1250.
        self._Qpow = 2.0

        # load calibration files
        try:
            d_intens = np.loadtxt(CALIBRATION_PATH / 'magik_intensity_hw106.refl')

            self.p_intens = np.polyfit(d_intens[:,0], d_intens[:,1], 3, w=1/d_intens[:,2])
        except OSError:
            warnings.warn('MAGIK calibration files not found, using defaults')
            self.p_intens = np.array([ 5.56637543e+02,  7.27944632e+04,  2.13479802e+02, -4.37052050e+01])

    def x2q(self, x):
        return x

    def x2a(self, x):
        return q2a(x, self._L)

    def qrange2xrange(self, bounds):
        return min(bounds), max(bounds)

    def intensity(self, x):
        news1 = self.get_slits(x)[0]
        incident_neutrons = np.polyval(self.p_intens, news1)
    
        return np.array(incident_neutrons, ndmin=2).T

    def T(self, x):

        x = np.array(x, ndmin=1)
        return np.broadcast_to(self.x2a(x), (len(self._L), len(x))).T

    def dT(self, x):
        x = np.array(x, ndmin=1)
        dTs = super().dT(x).T
        return np.broadcast_to(dTs, (len(self._L), len(x))).T

    def L(self, x):
        x = np.array(x, ndmin=1)
        return np.broadcast_to(self._L, (len(x), len(self._L)))

    def dL(self, x):
        x = np.array(x, ndmin=1)
        return np.broadcast_to(self._dL, (len(x), len(self._L)))

class CANDOR(ReflectometerBase):
    """ CANDOR Reflectometer with a single bank
    x = T """
    def __init__(self, bank=0) -> None:
        super().__init__()
        
        self.name = 'CANDOR'
        self.xlabel = r'$\Theta$ $(\degree)$'
        self.resolution = 'uniform'
        self.topspeed = 2.0
        self.basespeed = 0.1
        self.acceleration = 0.1
        # As of 1/24/2022:
        # Base: 0.1 deg / sec
        # Acceleration: 0.1 deg / sec^2
        # Top velocity: 2.0 deg / sec        
        # NOTE: dominated by acceleration and base for most moves!!

        # instrument geometry
        self._L12 = 4000.
        self._L2S = 356.
        self._LS3 = 356.
        self._L34 = 3000.
        self.footprint = 45.
        self._S3Offset = 5.0
        self._R12 = 2.5
        self.detector_mask = 8.0
        self.sample_width = np.inf

        # best practice Q scaling
        self._mon0 = 20.0
        self._mon1 = 20000.
        self._Qpow = 3.0

        # load wavelength calibration
        wvcal = np.flipud(np.loadtxt(CALIBRATION_PATH / f'DetectorWavelengths_PG_integrate_sumeff_bank{bank}.csv', delimiter=',', usecols=[1, 2]))
        self._L = wvcal[:,0]
        self._dL = wvcal[:,1]

        # load intensity calibration (same used for both banks for simulation purposes)
        with open(CALIBRATION_PATH / 'flowcell_d2o_r12_2_5_maxbeam_60_qoverlap0_751388_unpolarized_intensity.json', 'r') as f:
            d = json.load(f)
        
        self.intens_calib = np.squeeze(np.array(d['outputs'][0]['v']))
        self.s1_intens_calib = np.squeeze(d['outputs'][0]['x'])
        #ps1 = np.polynomial.polynomial.polyfit(s1, intens, 1)

    def x2q(self, x):
        return a2q(self.T(x), self.L(x))

    def x2a(self, x):
        return x

    def qrange2xrange(self, qbounds):
        qbounds = np.array(qbounds)
        minx = q2a(min(qbounds), max(self._L))
        maxx = q2a(max(qbounds), min(self._L))
        return minx, maxx

    def intensity(self, x):

        news1 = self.get_slits(x)[0]
        incident_neutrons = [np.interp(news1, self.s1_intens_calib, intens) for intens in self.intens_calib.T]
    
        return np.array(incident_neutrons, ndmin=2).T

    def meastime(self, x, totaltime):

        q = a2q(np.array(x), 5.0)
        f = self._mon0 + self._mon1 * q ** self._Qpow

        return totaltime * f / sum(f)

    def get_slits(self, x):
        s1, s2, s3, _ = super().get_slits(x)

        return s1, s2, s3, self.detector_mask

    def T(self, x):
        x = np.array(x, ndmin=1)
        return np.broadcast_to(x, (len(self._L), len(x))).T

    def dT(self, x):
        x = np.array(x, ndmin=1)
        dTs = super().dT(x).T
        return np.broadcast_to(dTs, (len(self._L), len(x))).T

    def L(self, x):
        x = np.array(x, ndmin=1)
        return np.broadcast_to(self._L, (len(x), len(self._L)))

    def dL(self, x):
        x = np.array(x, ndmin=1)
        return np.broadcast_to(self._dL, (len(x), len(self._L)))
    
