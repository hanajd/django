import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import List, Tuple

logger = logging.getLogger(__name__)


def _clear_mineru_output_dir(output_subdir: str) -> None:
    """每次运行前清空 -o 目录内容，避免残留产物触发 MinerU 重复处理或异常循环。"""
    if os.environ.get("MINERU_KEEP_OUTPUT", "").strip().lower() in ("1", "true", "yes"):
        return
    if not os.path.isdir(output_subdir):
        return
    try:
        for name in os.listdir(output_subdir):
            path = os.path.join(output_subdir, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.remove(path)
            except OSError:
                pass
    except OSError as e:
        logger.warning("清理 MinerU 输出目录失败（继续执行）: %s", e)


def _run_mineru_subprocess(
    cmd: List[str], timeout_s: int
) -> Tuple[int, str, str]:
    """
    运行 mineru；POSIX 下使用新会话，超时或失败时可结束整个进程组，减少子进程残留导致的「停不下来」。
    """
    use_session = sys.platform != "win32"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=use_session,
    )
    out, err = "", ""
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        logger.error("mineru 超时（%s 秒），正在终止进程组…", timeout_s)
        if use_session:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                proc.communicate()
        else:
            proc.kill()
            proc.communicate()
        raise
    return proc.returncode, out or "", err or ""


def _nvidia_gpu_available() -> bool:
    try:
        r = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return False


def resolve_mineru_backend() -> str:
    if "MINERU_BACKEND" in os.environ:
        return os.environ["MINERU_BACKEND"].strip()
    return "hybrid-auto-engine" if _nvidia_gpu_available() else "pipeline"


def find_mineru_markdown(output_root: str) -> str:
    if not os.path.isdir(output_root):
        return ""
    candidates = []
    for root, _, files in os.walk(output_root):
        for fn in files:
            if fn.lower().endswith(".md"):
                candidates.append(os.path.join(root, fn))
    if not candidates:
        return ""
    skip_sub = ("middle", "layout", "span", "model")
    scored = []
    for p in candidates:
        if any(x in os.path.basename(p).lower() for x in skip_sub):
            continue
        try:
            scored.append((os.path.getsize(p), p))
        except Exception:
            pass
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored else candidates[0]


def find_mineru_layout_pdf(output_root: str) -> str:
    for root, _, files in os.walk(output_root):
        for f in files:
            if "layout" in f.lower() and f.endswith(".pdf"):
                return os.path.join(root, f)
    return ""


def find_mineru_extracted_images(output_root: str) -> List[str]:
    possible_paths = [
        os.path.join(output_root, "hybrid_auto", "images"),
        os.path.join(output_root, "auto", "hybrid_auto", "images"),
        os.path.join(output_root, "images"),
    ]
    try:
        for name in os.listdir(output_root):
            sub = os.path.join(output_root, name)
            if not os.path.isdir(sub):
                continue
            possible_paths.append(os.path.join(sub, "hybrid_auto", "images"))
            possible_paths.append(os.path.join(sub, "auto", "hybrid_auto", "images"))
            possible_paths.append(os.path.join(sub, "images"))
    except OSError:
        pass
    seen = set()
    deduped = []
    for p in possible_paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    possible_paths = deduped

    img_exts = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".JPG", ".JPEG", ".PNG")
    extracted_img_list = []

    for target_dir in possible_paths:
        if os.path.isdir(target_dir):
            logger.info("找到MinerU图片目录: %s", target_dir)
            for file_name in os.listdir(target_dir):
                if file_name.lower().endswith(img_exts):
                    full_path = os.path.join(target_dir, file_name)
                    extracted_img_list.append(full_path)
                    logger.info("找到提取图片: %s", full_path)

    if not extracted_img_list:
        logger.info("兜底搜索MinerU输出目录下的所有图片: %s", output_root)
        for root, _, files in os.walk(output_root):
            for file_name in files:
                if file_name.lower().endswith(img_exts) and "layout" not in file_name.lower():
                    full_path = os.path.join(root, file_name)
                    extracted_img_list.append(full_path)
                    logger.info("兜底找到图片: %s", full_path)

    extracted_img_list = list(set(extracted_img_list))
    extracted_img_list.sort()
    logger.info("共找到 %s 张MinerU提取图片", len(extracted_img_list))
    return extracted_img_list


def read_md_file(md_path: str) -> str:
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def convert_pdf_to_md(pdf_path: str, output_subdir: str) -> str:
    mineru_cmd = shutil.which("mineru")
    if not mineru_cmd:
        logger.error("mineru未安装或未配置环境变量")
        return ""
    os.makedirs(output_subdir, exist_ok=True)
    backend = resolve_mineru_backend()
    try:
        timeout_s = int(os.environ.get("MINERU_TIMEOUT", "600"))
    except ValueError:
        timeout_s = 600
    cmd = [mineru_cmd, "-p", os.path.abspath(pdf_path), "-o", os.path.abspath(output_subdir)]
    if backend:
        cmd.extend(["-b", backend])
    _clear_mineru_output_dir(output_subdir)
    try:
        logger.info(
            "执行MinerU: %s（GPU 检测: %s）",
            " ".join(cmd),
            "可用" if _nvidia_gpu_available() else "未检测到或未安装驱动",
        )
        returncode, stdout, stderr = _run_mineru_subprocess(cmd, timeout_s)
        if returncode != 0:
            logger.error(
                "mineru 退出码 %s\nstderr:\n%s\nstdout 末尾:\n%s",
                returncode,
                (stderr or "")[-4000:],
                (stdout or "")[-2000:],
            )
            return ""
        md_path = find_mineru_markdown(output_subdir)
        if not md_path:
            for _ in range(15):
                time.sleep(1)
                md_path = find_mineru_markdown(output_subdir)
                if md_path:
                    logger.info("MinerU MD 在轮询后出现")
                    break
        if not md_path:
            out_tail = (stdout or "")[-2500:]
            err_tail = (stderr or "")[-2500:]
            logger.warning("MinerU 进程成功结束但未发现 .md。stdout 末尾:\n%s", out_tail)
            logger.warning("stderr 末尾:\n%s", err_tail)
        logger.info("MinerU转换完成，MD文件: %s", md_path)
        return md_path
    except subprocess.TimeoutExpired:
        logger.error("mineru 已终止（超时 %s 秒）；可调大环境变量 MINERU_TIMEOUT", timeout_s)
        return ""
    except Exception as e:
        logger.error("mineru转换失败: %s", e)
        return ""
