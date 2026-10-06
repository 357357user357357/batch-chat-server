"""Hecke-operator math pinned against classical values (exact checks)."""

import math
from fractions import Fraction

import pytest

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


# ------------------------------------------------------------------ L-values

def test_l_value_dirichlet_truncation_basics():
    mm.delta(60)  # warm nothing; just exercise import path
    d = mm.delta(120)
    v = mm.l_value(d, 9.0)
    assert v != 0.0
    # truncation monotonicity: more terms move the value toward convergence
    v2 = mm.l_value(d, 9.0, terms=119)
    assert abs(v2 - v) < 1e-6
    with pytest.raises(ValueError):
        mm.l_value(d, 0.0)
    with pytest.raises(ValueError):
        mm.l_value(mm.eisenstein(12, 20), 9.0)  # nonzero constant term


def test_dirichlet_and_mellin_routes_agree():
    d = mm.delta(600)
    s = 9.0
    direct = mm.l_value(d, s, terms=599)
    via_mellin = mm.completed_l(d, s).real * (2 * math.pi) ** s / math.gamma(s)
    assert abs(direct - via_mellin) < 1e-6 * max(1.0, abs(direct))


def test_completed_l_functional_equation():
    # Lambda(s) = i^k Lambda(k-s); for k=12 (i^12 = 1) both sides are real
    # and computed from DIFFERENT integrands — a real numeric check.
    d = mm.delta(120)
    lam8 = mm.completed_l(d, 8.0)
    lam4 = mm.completed_l(d, 4.0)
    assert abs(lam8.imag) < 1e-9 and abs(lam4.imag) < 1e-9
    assert abs(lam4.real - lam8.real) < 1e-6 * abs(lam8.real)
    lam7 = mm.completed_l(d, 7.0)
    lam5 = mm.completed_l(d, 5.0)
    assert abs(lam5.real - lam7.real) < 1e-6 * abs(lam7.real)


def test_l_any_reaches_the_critical_strip():
    d = mm.delta(600)
    # right of the wall: equals the Dirichlet truncation
    assert abs(mm.l_any(d, 9.0) - mm.l_value(d, 9.0, terms=599)) < 1e-9
    # critical strip: converges to the same value the Mellin route gives
    s = 6.0
    expected = mm.completed_l(d, s).real * (2 * math.pi) ** s / math.gamma(s)
    assert abs(mm.l_any(d, s) - expected) < 1e-12
    # L(Delta, 6) is not zero and not absurdly large (tau ~ n^5.5 decay)
    assert 0.0 < abs(expected) < 100.0


def test_completed_l_requires_cusp_and_level_one():
    with pytest.raises(ValueError):
        mm.completed_l(mm.eisenstein(12, 20), 6.0)
    f = mm.QExpansion(12, [0, 1, 1], level=2)
    with pytest.raises(NotImplementedError):
        mm.completed_l(f, 6.0)
