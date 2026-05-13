"""
多 GPU 环境下 MinerU / Ollama 调用侧的轻量调度：

- 通过 nvidia-smi 读取每卡空闲显存与利用率，为 MinerU 子进程选择较空闲的卡（CUDA_VISIBLE_DEVICES）。
- 为 Ollama 请求动态合并 runner options（num_ctx / num_gpu），在显卡繁忙或显存紧张时自动收紧，减少与机上其它任务抢显存。

说明：Ollama 为独立服务进程，本模块无法在服务端替其「全局限制显存」，但在每次 generate/chat 传入的 options
可被 llama.cpp 用于该次推理的上下文与 GPU 层数，从而在多任务共享同一 GPU 时更稳妥。

环境变量（均为可选）::

    PIPELINE_GPU_AUTO_MINERU=1       # 是否自动为 MinerU 子进程选卡（默认开）
    PIPELINE_GPU_AUTO_OLLAMA=1      # 是否按 GPU 情况收紧 Ollama options（默认开）
    MINERU_CUDA_VISIBLE_DEVICES=     # 若设置则强制使用该值，跳过自动选卡
    # 若进程级 CUDA_VISIBLE_DEVICES 已设置，则不再改动（尊重容器/用户全局绑定）

    OLLAMA_PREFERRED_GPU_INDEX=     # 评估 Ollama 显存余量时参考哪块卡；不设则取空闲显存最大的卡

    PIPELINE_GPU_BUSY_UTIL_THRESHOLD=75   # 利用率超过此值视为「忙」
    PIPELINE_GPU_VERY_BUSY_UTIL=90
    PIPELINE_GPU_LOW_FREE_MB=6144         # 空闲低于此 MB 视为「非常紧」
    PIPELINE_GPU_MED_FREE_MB=12288

    PIPELINE_OLLAMA_NUM_CTX_IDLE=8192
    PIPELINE_OLLAMA_NUM_CTX_MED=4096
    PIPELINE_OLLAMA_NUM_CTX_BUSY=2048
    PIPELINE_OLLAMA_NUM_CTX_LOW_FRAC=6144 # 空闲比例低时的 ctx 上限

    PIPELINE_OLLAMA_NUM_GPU_BUSY_CAP=48   # 极忙时 num_gpu（层数）上限
    PIPELINE_OLLAMA_NUM_GPU_MED_CAP=64

    OLLAMA_DYNAMIC_SHRINK_USER_CTX=1      # 若 OLLAMA_OPTIONS 已指定 num_ctx，忙时是否允许再压低（默认开）
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = True) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v == "":
        return default
    return v not in ("0", "false", "no", "off")


def query_nvidia_gpu_stats() -> List[Dict[str, Any]]:
    """返回 [{index, free_mb, total_mb, used_mb, util}, ...]；失败返回 []."""
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return []
        out: List[Dict[str, Any]] = []
        for line in (r.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                out.append(
                    {
                        "index": int(parts[0]),
                        "free_mb": float(parts[1]),
                        "total_mb": float(parts[2]),
                        "used_mb": float(parts[3]),
                        "util": float(parts[4]),
                    }
                )
            except (ValueError, TypeError):
                continue
        return out
    except Exception as e:
        logger.debug("nvidia-smi query failed: %s", e)
        return []


def _score_gpu(g: Dict[str, Any]) -> float:
    """分数越高越适合接新任务。"""
    total = max(float(g["total_mb"]), 1.0)
    free_ratio = float(g["free_mb"]) / total
    util = float(g["util"])
    return free_ratio * 1000.0 - util * 2.0


def pick_mineru_cuda_visible_devices() -> Optional[str]:
    """
    返回写入 MinerU 子进程环境的 CUDA_VISIBLE_DEVICES（单卡索引）。
    None 表示不修改子进程环境。
    """
    if not _truthy("PIPELINE_GPU_AUTO_MINERU", True):
        return None
    explicit = (os.environ.get("MINERU_CUDA_VISIBLE_DEVICES") or "").strip()
    if explicit:
        return explicit
    if (os.environ.get("CUDA_VISIBLE_DEVICES") or "").strip():
        return None
    stats = query_nvidia_gpu_stats()
    if not stats:
        return None
    best = max(stats, key=_score_gpu)
    chosen = str(int(best["index"]))
    logger.info(
        "PIPELINE_GPU_AUTO_MINERU: 选用 GPU %s（空闲 %.0f / %.0f MB，利用率 %.0f%%）",
        chosen,
        best["free_mb"],
        best["total_mb"],
        best["util"],
    )
    return chosen


def mineru_subprocess_env() -> Dict[str, str]:
    cuda = pick_mineru_cuda_visible_devices()
    if cuda is None:
        return {}
    return {"CUDA_VISIBLE_DEVICES": cuda}


def _ollama_reference_gpu(stats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not stats:
        return None
    raw = (os.environ.get("OLLAMA_PREFERRED_GPU_INDEX") or "").strip()
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        idx = int(raw)
        for g in stats:
            if int(g["index"]) == idx:
                return g
    return max(stats, key=lambda x: float(x["free_mb"]))


def merge_ollama_options_for_gpu_headroom(base: Dict[str, Any]) -> Dict[str, Any]:
    """
    按当前 GPU 余量收紧 num_ctx，必要时压低 num_gpu（层数），减轻争用。
    """
    if not _truthy("PIPELINE_GPU_AUTO_OLLAMA", True):
        return dict(base)
    stats = query_nvidia_gpu_stats()
    g = _ollama_reference_gpu(stats)
    out = dict(base)
    if g is None:
        return out

    free_mb = float(g["free_mb"])
    util = float(g["util"])
    total = max(float(g["total_mb"]), 1.0)
    frac = free_mb / total

    busy_u = float(os.environ.get("PIPELINE_GPU_BUSY_UTIL_THRESHOLD", "75"))
    very_busy_u = float(os.environ.get("PIPELINE_GPU_VERY_BUSY_UTIL", "90"))
    low_free = float(os.environ.get("PIPELINE_GPU_LOW_FREE_MB", "6144"))
    med_free = float(os.environ.get("PIPELINE_GPU_MED_FREE_MB", "12288"))

    very_tight = util >= very_busy_u or free_mb < low_free
    tight = util >= busy_u or free_mb < med_free

    if very_tight:
        num_ctx_cap = int(os.environ.get("PIPELINE_OLLAMA_NUM_CTX_BUSY", "2048"))
    elif tight or frac < 0.35:
        num_ctx_cap = int(os.environ.get("PIPELINE_OLLAMA_NUM_CTX_MED", "4096"))
    elif frac < 0.45:
        num_ctx_cap = int(os.environ.get("PIPELINE_OLLAMA_NUM_CTX_LOW_FRAC", "6144"))
    else:
        num_ctx_cap = int(os.environ.get("PIPELINE_OLLAMA_NUM_CTX_IDLE", "8192"))

    user_ctx = out.get("num_ctx")
    if user_ctx is not None:
        try:
            uc = int(user_ctx)
            if _truthy("OLLAMA_DYNAMIC_SHRINK_USER_CTX", True):
                out["num_ctx"] = min(uc, num_ctx_cap)
            else:
                out["num_ctx"] = uc
        except (TypeError, ValueError):
            pass
    else:
        out["num_ctx"] = num_ctx_cap

    try:
        ng_raw = out.get("num_gpu", 999)
        ng_int = int(ng_raw) if ng_raw is not None else 999
    except (TypeError, ValueError):
        ng_int = 999

    if very_tight:
        cap = int(os.environ.get("PIPELINE_OLLAMA_NUM_GPU_BUSY_CAP", "48"))
        new_ng = min(ng_int, cap)
        if new_ng < ng_int:
            out["num_gpu"] = new_ng
            logger.info(
                "PIPELINE_GPU_AUTO_OLLAMA: 显存/负载紧张，num_gpu %s → %s（GPU%s）",
                ng_int,
                new_ng,
                g["index"],
            )
    elif tight:
        cap = int(os.environ.get("PIPELINE_OLLAMA_NUM_GPU_MED_CAP", "64"))
        new_ng = min(ng_int, cap)
        if new_ng < ng_int:
            out["num_gpu"] = new_ng

    logger.info(
        "PIPELINE_GPU_AUTO_OLLAMA: 参考 GPU%s num_ctx=%s num_gpu=%s（空闲 %.0fMB 利用率 %.0f%%）",
        g["index"],
        out.get("num_ctx"),
        out.get("num_gpu"),
        free_mb,
        util,
    )
    return out
