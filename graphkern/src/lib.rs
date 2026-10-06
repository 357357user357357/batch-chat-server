//! graphkern — CPU graph kernel behind a C ABI for flexchat.top.
//!
//! cugraph-style design without CUDA: the caller (Python, via ctypes)
//! hands over a CSR adjacency in flat arrays plus pre-allocated output
//! buffers; nothing is allocated across the FFI boundary and no handle
//! is kept — every call is self-contained.
//!
//! Conventions:
//! - Node indices are 0-based `u32`; offsets are `u32` of length n+1.
//! - Parallel (duplicate) targets are the caller's business — the kernel
//!   preserves whatever multiplicities it is given (PageRank/PPR treat
//!   them as extra weight; Brandes needs a deduped CSR from the caller).
//! - All results are deterministic: per-node work gathers in a fixed
//!   order over a reverse CSR built identically on every call, so
//!   threading (rayon) never changes the answer.
//!
//! SPDX-License-Identifier: MIT

use rayon::prelude::*;
use std::os::raw::{c_char, c_double, c_int, c_uint};

// ---------------------------------------------------------------- CSR view

struct Csr {
    n: usize,
    offsets: Vec<usize>,
    targets: Vec<u32>,
    out_deg: Vec<f64>,
}

/// # Safety
/// `offsets` must be readable for `off_len` elements, `targets` for
/// `tgt_len`; `off_len` must be n+1 with offsets[0]==0 and monotone,
/// offsets[n]==tgt_len, every target < n. Returns None on any violation.
unsafe fn read_csr(
    n: usize,
    offsets: *const c_uint,
    off_len: usize,
    targets: *const c_uint,
    tgt_len: usize,
) -> Option<Csr> {
    if n == 0 || off_len != n + 1 || offsets.is_null() || (tgt_len > 0 && targets.is_null()) {
        return None;
    }
    let mut offs = Vec::with_capacity(off_len);
    for i in 0..off_len {
        offs.push(*offsets.add(i) as usize);
    }
    if offs[0] != 0 || offs[n] != tgt_len {
        return None;
    }
    if offs.windows(2).any(|w| w[0] > w[1]) {
        return None;
    }
    let mut tgts = Vec::with_capacity(tgt_len);
    for i in 0..tgt_len {
        let t = *targets.add(i) as usize;
        if t >= n {
            return None;
        }
        tgts.push(t as u32);
    }
    let out_deg: Vec<f64> = (0..n)
        .map(|i| {
            let d = offs[i + 1] - offs[i];
            if d == 0 { 0.0 } else { d as f64 }
        })
        .collect();
    Some(Csr {
        n,
        offsets: offs,
        targets: tgts,
        out_deg,
    })
}

/// Reverse CSR (incoming edges), built deterministically: sources are
/// scanned in ascending node order so each node's incoming list is sorted.
fn reverse_csr(csr: &Csr) -> (Vec<usize>, Vec<u32>) {
    let n = csr.n;
    let mut in_deg = vec![0usize; n + 1];
    for &t in &csr.targets {
        in_deg[t as usize + 1] += 1;
    }
    for i in 0..n {
        in_deg[i + 1] += in_deg[i];
    }
    let mut rev = vec![0u32; csr.targets.len()];
    let mut cursor = in_deg[..n].to_vec();
    for i in 0..n {
        for k in csr.offsets[i]..csr.offsets[i + 1] {
            let t = csr.targets[k] as usize;
            rev[cursor[t]] = i as u32;
            cursor[t] += 1;
        }
    }
    (in_deg, rev)
}

// ---------------------------------------------------------------- PageRank

/// Power-iteration PageRank with dangling mass redistributed uniformly.
/// `out` receives n f64 ranks summing to 1.
///
/// # Safety
/// See [`read_csr`]; `out` must be writable for n elements.
#[no_mangle]
pub unsafe extern "C" fn gk_pagerank(
    n: usize,
    offsets: *const c_uint,
    off_len: usize,
    targets: *const c_uint,
    tgt_len: usize,
    damping: c_double,
    max_iter: c_uint,
    tol: c_double,
    out: *mut c_double,
) -> c_int {
    if out.is_null() {
        return -1;
    }
    let Some(csr) = read_csr(n, offsets, off_len, targets, tgt_len) else {
        return -2;
    };
    let (in_off, in_tgt) = reverse_csr(&csr);

    let mut rank: Vec<f64> = vec![1.0 / n as f64; n];
    let damping = if damping <= 0.0 || damping >= 1.0 { 0.85 } else { damping };
    let max_iter = if max_iter == 0 { 50 } else { max_iter };

    for _ in 0..max_iter {
        let dangling: f64 = (0..n)
            .filter(|&i| csr.offsets[i + 1] == csr.offsets[i])
            .map(|i| rank[i])
            .sum();
        let base = (1.0 - damping) / n as f64 + damping * dangling / n as f64;

        // Per-node gather over incoming edges — independent, deterministic.
        let new: Vec<f64> = (0..n)
            .into_par_iter()
            .map(|i| {
                let mut acc = base;
                for k in in_off[i]..in_off[i + 1] {
                    let j = in_tgt[k] as usize;
                    acc += damping * rank[j] / csr.out_deg[j];
                }
                acc
            })
            .collect();

        let delta: f64 = (0..n).map(|i| (new[i] - rank[i]).abs()).sum();
        rank = new;
        if delta < tol {
            break;
        }
    }
    std::ptr::copy_nonoverlapping(rank.as_ptr(), out, n);
    0
}

// --------------------------------------------------- personalized PageRank

/// PPR from a seed set: teleportation mass is spread uniformly over
/// `seeds`; dangling mass returns to the seed distribution (standard
/// choice for recommendation-style queries). `out` receives n scores.
///
/// # Safety
/// See [`read_csr`]; `seeds` readable for `n_seeds` (each < n);
/// `out` writable for n.
#[no_mangle]
pub unsafe extern "C" fn gk_ppr(
    n: usize,
    offsets: *const c_uint,
    off_len: usize,
    targets: *const c_uint,
    tgt_len: usize,
    seeds: *const c_uint,
    n_seeds: usize,
    damping: c_double,
    max_iter: c_uint,
    tol: c_double,
    out: *mut c_double,
) -> c_int {
    if out.is_null() || (n_seeds > 0 && seeds.is_null()) {
        return -1;
    }
    let Some(csr) = read_csr(n, offsets, off_len, targets, tgt_len) else {
        return -2;
    };
    if n_seeds == 0 {
        return -3;
    }
    let mut seed_vec = vec![0u32; n_seeds];
    for (i, s) in seed_vec.iter_mut().enumerate() {
        *s = *seeds.add(i);
        if *s as usize >= n {
            return -3;
        }
    }
    let (in_off, in_tgt) = reverse_csr(&csr);

    let mut teleport = vec![0.0f64; n];
    for &s in &seed_vec {
        teleport[s as usize] += 1.0 / n_seeds as f64;
    }
    let dangling_seed_mass: f64 = (0..n)
        .filter(|&i| csr.offsets[i + 1] == csr.offsets[i])
        .map(|i| teleport[i])
        .sum();

    let damping = if damping <= 0.0 || damping >= 1.0 { 0.85 } else { damping };
    let max_iter = if max_iter == 0 { 50 } else { max_iter };

    let mut p = teleport.clone();
    for _ in 0..max_iter {
        let dangling: f64 = (0..n)
            .filter(|&i| csr.offsets[i + 1] == csr.offsets[i])
            .map(|i| p[i])
            .sum();

        let new: Vec<f64> = (0..n)
            .into_par_iter()
            .map(|i| {
                let mut acc = (1.0 - damping) * teleport[i]
                    + damping * dangling_seed_mass * teleport[i]
                    + damping * dangling * teleport[i];
                for k in in_off[i]..in_off[i + 1] {
                    let j = in_tgt[k] as usize;
                    acc += damping * p[j] / csr.out_deg[j];
                }
                acc
            })
            .collect();

        let delta: f64 = (0..n).map(|i| (new[i] - p[i]).abs()).sum();
        p = new;
        if delta < tol {
            break;
        }
    }
    std::ptr::copy_nonoverlapping(p.as_ptr(), out, n);
    0
}

// -------------------------------------------------------------- betweenness

/// Brandes betweenness on a (deduped) directed CSR, unweighted BFS.
/// Parallelism is chunked: each chunk accumulates into its own buffer and
/// chunk buffers are summed in fixed order — same answer every run.
///
/// # Safety
/// See [`read_csr`]; `out` writable for n.
#[no_mangle]
pub unsafe extern "C" fn gk_betweenness(
    n: usize,
    offsets: *const c_uint,
    off_len: usize,
    targets: *const c_uint,
    tgt_len: usize,
    out: *mut c_double,
) -> c_int {
    if out.is_null() {
        return -1;
    }
    let Some(csr) = read_csr(n, offsets, off_len, targets, tgt_len) else {
        return -2;
    };
    let nthreads = rayon::current_num_threads().max(1);
    let chunk = n.div_ceil(nthreads);

    // Chunks of sources, one local buffer per chunk, fixed chunk count.
    let chunk_ids: Vec<usize> = (0..nthreads).collect();
    let buffers: Vec<Vec<f64>> = chunk_ids
        .into_par_iter()
        .map(|c| brandes_chunk(&csr, c * chunk, ((c + 1) * chunk).min(n)))
        .collect();

    let mut cb = vec![0.0f64; n];
    for buf in &buffers {
        for i in 0..n {
            cb[i] += buf[i];
        }
    }
    std::ptr::copy_nonoverlapping(cb.as_ptr(), out, n);
    0
}

fn brandes_chunk(csr: &Csr, lo: usize, hi: usize) -> Vec<f64> {
    let n = csr.n;
    let mut cb = vec![0.0f64; n];
    if lo >= hi {
        return cb;
    }
    let mut dist = vec![-1i32; n];
    let mut sigma = vec![0.0f64; n];
    let mut delta = vec![0.0f64; n];
    let mut order: Vec<u32> = Vec::with_capacity(n);
    let mut pred: Vec<Vec<u32>> = vec![Vec::new(); n];
    let mut queue: Vec<u32> = Vec::with_capacity(n);

    for s in lo..hi {
        // reset only touched entries would be faster; n is small here
        for i in 0..n {
            dist[i] = -1;
            sigma[i] = 0.0;
            delta[i] = 0.0;
            pred[i].clear();
        }
        order.clear();
        queue.clear();
        dist[s] = 0;
        sigma[s] = 1.0;
        queue.push(s as u32);
        order.push(s as u32);

        let mut qi = 0usize;
        while qi < queue.len() {
            let u = queue[qi] as usize;
            qi += 1;
            for k in csr.offsets[u]..csr.offsets[u + 1] {
                let v = csr.targets[k] as usize;
                if dist[v] < 0 {
                    dist[v] = dist[u] + 1;
                    queue.push(v as u32);
                    order.push(v as u32);
                }
                if dist[v] == dist[u] + 1 {
                    sigma[v] += sigma[u];
                    pred[v].push(u as u32);
                }
            }
        }

        for &w in order.iter().rev() {
            let w = w as usize;
            for &v in &pred[w] {
                let v = v as usize;
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
            }
            if w != s {
                cb[w] += delta[w];
            }
        }
    }
    cb
}

// ------------------------------------------------------------------ misc

/// Writes a short version string, NUL-terminated, into `buf`. Returns the
/// number of bytes written (excluding NUL), or the needed length if `buf`
/// is null / capacity too small (positive), negative on error.
///
/// # Safety
/// `buf` must be writable for `cap` bytes when non-null.
#[no_mangle]
pub unsafe extern "C" fn gk_version(buf: *mut c_char, cap: usize) -> c_int {
    static V: &str = concat!(env!("CARGO_PKG_VERSION"));
    let need = V.len() as c_int;
    if buf.is_null() || cap < V.len() + 1 {
        return need;
    }
    std::ptr::copy_nonoverlapping(V.as_ptr() as *const c_char, buf, V.len());
    *buf.add(V.len()) = 0;
    need
}
