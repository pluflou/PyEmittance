# Module containing functions for beam optics calculations
import numpy as np
from numpy import sin, cos, sinh, cosh, sqrt
import scipy.linalg
# TODO update fns
from scipy.constants import c as c_light, m_e as m0
import matplotlib.pyplot as plt
from pyemittance.beam_io import get_rmat

def get_gradient(b_field, l_eff=0.108):
    """
    Calculates quadrupole gradient from B field.

    Parameters
    ----------
    b_field: ndarray
        Integrated field [kG]
    l_eff: float
        Effective length [m]

    Returns
    -------
    array
    Quad field gradient [T/m]
    """
    return np.array(b_field) * 0.1 / l_eff

def get_k1(g, energy=0.135, m_0=0.000511):
    """
    Calculates quadrupole strength from gradient.

    Parameters
    ----------
    g: ndarray
        Quad field gradient [T/m]
    energy: float
        Beam energy [GeV]
        # TODO: update to eV
    m_0: float
        Electron mass [GeV]
        # TODO: update to kg

    Returns
    -------
    ndarray
    Quad strength [1/m^2]

    """

    gamma = energy / m_0
    beta = np.sqrt(1 - 1 / gamma ** 2)
    return 0.2998 * g / energy / beta

def normalize_emit(emit, err, energy=0.135, m_0=0.000511):
    gamma = energy / m_0
    beta = np.sqrt(1 - 1 / gamma ** 2)
    return emit*gamma*beta, err*gamma*beta

def get_kL(quad_vals, l=0.108, energy=0.135, m_0=0.000511):
    kL = get_k1(get_gradient(quad_vals), energy=energy, m_0=m_0) * l
    return kL

def get_quad_field(k, energy=0.135, l=0.108, m_0=0.000511):
    """Get quad field [kG] from k1 [1/m^2]"""

    gamma = energy / m_0
    beta = np.sqrt(1 - 1 / gamma ** 2)
    return np.array(k) * l / 0.1 / 0.2998 * energy * beta

def thin_quad_mat2(kL):
    """
    Quad transport matrix, 2x2, assuming thin quad
    :param kL: quad strength * quad length (1/m)
    :return: thin quad transport matrix
    """
    return np.array([[1, 0], [-kL, 1]])

def r_mat2():
    """
    Transport matrix after quad to screen, 2x2
    """
    return get_rmat()

def quad_mat2(kL, L=0):
    """
    Quadrupole transfer matrix, 2x2, assuming some quad thickness
    L = 0 returns thin quad matrix
    :param kL: quad strength * quad length (1/m)
    :param L: quad length (m)
    :return: thick quad transport matrix
    """

    if L == 0:
        return thin_quad_mat2(kL)

    k = kL / L

    if k == 0:
        mat2 = r_mat2()
    elif k > 0:
        # Focusing
        rk = sqrt(k)
        phi = rk * L
        mat2 = [[cos(phi), sin(phi) / rk], [-rk * sin(phi), cos(phi)]]
    else:
        # Defocusing
        rk = sqrt(-k)
        phi = rk * L
        mat2 = [[cosh(phi), sinh(phi) / rk], [rk * sinh(phi), cosh(phi)]]

    return mat2

def quad_rmat_mat2(kL, Lquad=0):
    """
    Composite [quad, drift] 2x2 transfer matrix
    :param kL: quad strength * quad length (1/m)
    :param Lquad: quad length (m)
    :return:
    """

    return r_mat2() @ quad_mat2(kL, Lquad)

def propagate_sigma(mat2_init, mat2_ele):
    """
    Propagate a transport matrix through beamline from point A to B
    :param sigma_mat2: 2x2 matrix at A
    :param mat2: total 2x2 trasport matrix of elements between point A and B
    :return: 2x2 matrix at B
    """
    return (mat2_ele @ mat2_init) @ mat2_ele.T

def estimate_sigma_mat_thick_quad(sizes, kLlist, sizes_err=None, weights=None, Lquad=0.108, calc_bmag=False, plot=True):
    """
    Estimates the beam sigma matrix at a screen by scanning an upstream quad.
    This models the system as a thick quad.
    :param sizes: measured beam sizes at the screen
    :param kLlist: kL of the upstream quad
    :param weights: If present, each measurement will be weighted by this factor.
    :param Lquad:  length of the quadrupole magnet (m)
    :param plot: bool to plot ot not
    :return: emittance, sig11, sig12 and sig22 at measurement screen
    """

    # measurement vector
    sizes = np.array(sizes)
    b = sizes ** 2
    n = len(b)

    # Fill in defaults, checking.
    if weights is None:
        weights = np.ones(n)
    assert len(weights) == n

    # Multiply by weights. This should correspond to the other weight multiplication below
    weights = np.array(weights)
    b = weights * sizes ** 2

    # form B matrix
    B = []
    # Collect mat2 for later
    mat2s = []
    for kL, weight in zip(kLlist, weights):
        mat2 = quad_rmat_mat2(kL, Lquad=Lquad)
        mat2s.append(mat2)
        r11, r12, r21, r22 = mat2.flatten()
        r_mat_factor = np.array([r11 ** 2, 2 * r11 * r12, r12 ** 2])
        B.append(r_mat_factor * weight)  # corresponding weight multiplication

    B = np.array(B)

    # Invert (pseudoinverse)
    s11, s12, s22 = scipy.linalg.pinv(B) @ b

    # Twiss calc just before the quad
    emit2 = s11 * s22 - s12 ** 2

    #return NaN if emit can't be calculated
    if emit2 < 0:
        print("NaN")
        return [np.nan]

    emit = np.sqrt(emit2)
    beta = s11 / emit
    alpha = -s12 / emit

    # Get error on emittance from fitted params
    emit_err, beta_err, alpha_err = get_twiss_error(emit, s11, s12, s22, B)

    if plot or calc_bmag:
        s11_screen, s12_screen, s22_screen = propagate_to_screen(s11, s12, s22, kLlist, mat2s, 
                                                                 Lquad, sizes, sizes_err, emit, plot)
        return [emit, emit_err, beta_err / beta, alpha_err / alpha, s11_screen, s12_screen, s22_screen]

    return [emit, emit_err, beta_err/beta, alpha_err/alpha]

def propagate_to_screen(s11, s12, s22, kLlist, mat2s, Lquad, sizes, sizes_err, emit, plot):
    # Matrix form for propagation
    sigma0 = np.array([[s11, s12], [s12, s22]])

    # Propagate forward to the screen
    s11_screen = []
    s12_screen = []
    s22_screen = []
    for kL, mat2 in zip(kLlist, mat2s):
        sigma1 = propagate_sigma(sigma0, mat2)
        s11_screen.append(sigma1[0, 0])
        s12_screen.append(sigma1[0, 1])
        s22_screen.append(sigma1[1, 1])
    s11_screen = np.array(s11_screen)
    s12_screen = np.array(s12_screen)
    s22_screen = np.array(s22_screen)

    if plot:
        # Plot the data
        quad = get_quad_field(kLlist / Lquad)
        plt.errorbar(quad, sizes, yerr=sizes_err, fmt='o', label=f'Measurements')

        # Model prediction
        plt.errorbar(quad, np.sqrt(s11_screen), marker='.', label=f'Model')

        plt.xlabel('B (kG)')
        plt.ylabel(f'sizes (m)')
        plt.legend()
        plt.show()
        plt.close()

    return s11_screen, s12_screen, s22_screen

def twiss_and_bmag(sig11, sig12, sig22, beta_err, alpha_err, beta0=1, alpha0=0):
    """
    Calculates Twiss ang Bmag from the sigma matrix.
    """

    # Twiss at screen
    emit  = np.sqrt(sig11 * sig22 - sig12**2)
    beta  = sig11/emit
    alpha = -sig12/emit
    gamma = sig22/emit

    # Form bmag
    gamma0 = (1 + alpha0 ** 2) / beta0
    bmag = (beta * gamma0 - 2 * alpha * alpha0 + gamma * beta0) / 2
    # Add err in quadrature (assuming twiss0 has no uncertainty)
    # Taking relative error as measured at quad
    bmag_err = bmag * np.sqrt( (beta_err)**2 + (alpha_err)**2 )

    d = {}
    d['emit'] = emit
    d['beta'] = beta
    d['alpha'] = alpha
    d['bmag'] = bmag
    d['bmag_err'] = bmag_err

    return d

def gradient_mat3(emit, a1, a2, a3):
    """
    Gradient of f = { emittance, beta, alpha }
    where f is obtained at the scanning location (quad)
    :param eps: emittance parameter estimate
    :param a1: matrix element s11
    :param a2: matrix element s12
    :param a3: matrix element s22
    :return: gradient of f
    """

    emit_gradient = 1./(2*emit) * np.array( [a3, -2*a2, a1] )
    beta_gradient = 1./(2*emit**3) * np.array( [2*emit**4-a1*a3, 2*a2*a1, -a1**2] )
    alpha_gradient = -1./(2*emit) * np.array( [a2*a3, 2*emit**2-2*a2**2, a1*a2] )

    f_gradient = np.array( [emit_gradient, beta_gradient, alpha_gradient]).T

    return f_gradient

def get_fit_param_error(f_gradient, B):
    """
    Error estimation of the fitted params (s11, s12, s22)
    and the Twiss params. See 10.3204/DESY-THESIS-2005-014 p.10
    :param f_gradient: gradient of the 3-vector Twiss params
    :param B: B matrix incl. all scanned quad values
    :return: sqrt of the diagonal of the error matrix (emit_err, beta_err, alpha_err)
    """

    C = scipy.linalg.pinv( B.T @ B )

    error_matrix = f_gradient.T @ C @ f_gradient
    twiss_error = np.sqrt( np.diag( error_matrix ) )

    return twiss_error

def get_twiss_error(emit, a1, a2, a3, B):
    """
    Get error on the twiss params from fitted params
    :param emit: emittance parameter estimate
    :param a1: matrix element s11
    :param a2: matrix element s12
    :param a3: matrix element s22
    :param B:  B matrix incl. all scanned quad values
    :return: emit_err, beta_err, alpha_err
    """

    # get gradient of twiss params
    f_gradient =  gradient_mat3(emit, a1, a2, a3)
    # calculate errors on twiss from var and covar
    twiss_error = get_fit_param_error(f_gradient, B)

    return twiss_error
