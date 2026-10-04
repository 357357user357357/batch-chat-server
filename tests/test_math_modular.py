"""Hecke-operator math pinned against classical values (exact checks)."""

from fractions import Fraction

from app.services import math_modular as mm


def test_bernoulli_numbers():
    assert mm.bernoulli(0) == 1
    assert mm.bernoulli(1) == Fraction(-1, 2)
    assert mm.bernoulli(3) == 0
    assert mm.bernoulli(4) == Fraction(-1, 30)
    assert mm.bernoulli(6) == Fraction(1, 42)
    assert mm.bernoulli(12) == Fraction(-691, 2730)


def test_divisor_sigma():
    assert mm.sigma(1, 3) == 1
    assert mm.sigma(6, 3) == 252  # 1 + 8 + 27 + 216
    assert mm.sigma(4, 1) == 7  # 1 + 2 + 4


def test_eisenstein_e4_e6():
    e4 = mm.eisenstein(4, 8)
    assert e4.weight == 4
    assert e4.as_ints() == [1, 240, 2160, 6720, 17520, 30240, 60480, 82560]
    e6 = mm.eisenstein(6, 5)
    assert e6.as_ints() == [1, -504, -16632, -122976, -532728]


def test_eisenstein_e8_is_e4_squared():
    e4 = mm.eisenstein(4, 24)
    e8 = mm.eisenstein(8, 24)
    assert (e4 * e4).coeffs == e8.coeffs


def test_delta_ramanujan_tau():
    d = mm.delta(9)
    assert d.weight == 12
    assert d.as_ints() == [0, 1, -24, 252, -1472, 4830, -6048, -16744, 84480]


def test_hecke_eisenstein_eigenforms():
    # E_k is a Hecke eigenform with eigenvalue sigma_{k-1}(p) = 1 + p^{k-1}.
    for k, p in ((4, 2), (4, 3), (6, 2), (6, 5), (8, 7), (12, 3)):
        e = mm.eisenstein(k, 40)
        lam = mm.sigma(p, k - 1)
        res = mm.hecke(e, p)  # precision shrinks to the trusted window
        assert res.coeffs == e.scale(Fraction(lam)).coeffs[: res.precision()], (k, p)


def test_hecke_delta_eigenvalues_are_tau():
    d = mm.delta(32)
    for p in (2, 3, 5, 7, 11, 13):
        tau_p = d[p]  # normalized eigenform: T_p Delta = a(p) Delta
        res = mm.hecke(d, p)
        assert res.coeffs == d.scale(tau_p).coeffs[: res.precision()], p


def test_hecke_prime_power_t4_delta():
    # T_4 Delta = (tau(2)^2 - 2^11) Delta = -1472 Delta (trusted window).
    d = mm.delta(12)
    res = mm.hecke(d, 4)
    assert res.coeffs == d.scale(Fraction(-1472)).coeffs[: res.precision()]


def test_hecke_multiplicative_t2_t3_equals_t6():
    d = mm.delta(36)
    t23 = mm.hecke(mm.hecke(d, 2), 3)
    t32 = mm.hecke(mm.hecke(d, 3), 2)
    t6 = mm.hecke(d, 6)
    assert t23.coeffs == t6.coeffs
    assert t32.coeffs == t6.coeffs


def test_u_p_v_p_split_t_p():
    # T_p = U_p + p^{k-1} V_p, so U_p Delta + 2^11 V_p Delta = tau(2) Delta.
    d = mm.delta(16)
    combo = mm.u_p(d, 2) + mm.v_p(d, 2).scale(Fraction(2) ** 11)
    assert combo.coeffs == d.scale(Fraction(-24)).coeffs[: combo.precision()]


def test_hecke_constant_term_scales():
    # The constant term of T_p f is (1 + p^{k-1}) a(0).
    e4 = mm.eisenstein(4, 10)
    t2 = mm.hecke(e4, 2)
    assert t2[0] == 9 * e4[0]  # 1 + 2^3 = 9


def test_level_guard():
    f = mm.QExpansion(2, [1, 2, 3], level=7)
    try:
        mm.hecke(f, 2)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("expected NotImplementedError for level != 1")
