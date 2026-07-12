from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any


CNKI_SEARCH_URL = "https://kns.cnki.net/kns8s/defaultresult/index?kw={query}"

CNKI_RESULT_SELECTOR = (
    'a[href*="/kcms2/article/abstract" i], '
    'a[href*="kcms/detail/detail.aspx" i], '
    'a[href*="kns8s/Detail" i], '
    'a[href*="dbcode=" i], '
    '.result-table-list .name a, .result-table-list a.fz14, '
    '.search-result-item a[title], tr td.name a, .result-item a[title]'
)


def _compact(value: str) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if compact and any(marker in compact for marker in ("Ã", "Â", "ä", "å", "æ", "ç")):
        try:
            repaired = compact.encode("latin-1").decode("utf-8")
            if repaired:
                compact = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return compact


def _looks_like_result_url(url: str) -> bool:
    lowered = urllib.parse.unquote(url or "").lower()
    return any(
        marker in lowered
        for marker in (
            "/kcms2/article/abstract",
            "kcms/detail/detail.aspx",
            "kns8s/detail",
            "dbcode=",
            "filename=",
        )
    )


def _download_candidate_rank(candidate: dict[str, Any]) -> tuple[int, int]:
    label = str(candidate.get("text") or "")
    index = int(candidate["index"])
    if "PDF下载" in label:
        return (0, index)
    if "原版阅读" in label:
        return (1, index)
    if "CAJ下载" in label:
        return (2, index)
    if "HTML阅读" in label:
        return (3, index)
    return (4, index)


async def _empty_result_diagnostics(page: Any, query: str) -> dict[str, Any]:
    try:
        title = _compact(await page.title())
    except Exception:
        title = ""
    try:
        body = _compact(await page.locator("body").inner_text())[:800]
    except Exception:
        body = ""
    try:
        anchors = await page.locator("a").evaluate_all(
            """(links) => links.slice(0, 30).map((link) => ({
                text: (link.textContent || link.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                href: (link.href || link.getAttribute('href') || '').slice(0, 240)
            }))"""
        )
    except Exception:
        anchors = []
    lowered = body.lower()
    return {
        "query": query,
        "current_url": str(getattr(page, "url", "") or ""),
        "title": title,
        "body_preview": body[:320],
        "anchor_samples": anchors[:8],
        "challenge": any(marker in lowered for marker in ("验证码", "安全验证", "访问过于频繁", "captcha")),
        "login_required": any(marker in lowered for marker in ("请登录", "机构登录", "登录后", "login")),
        "explicit_empty": any(marker in lowered for marker in ("未检索到", "没有找到", "暂无数据", "0 条结果")),
    }


def _safe_download_name(title: str, suffix: str = ".pdf") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", title).strip("._")
    return f"{cleaned[:120] or 'cnki-paper'}{suffix}"


def _is_article_pdf(path: Path, source_url: str) -> bool:
    """Reject CNKI ad/placeholder PDFs while keeping short but real papers."""
    if "/ads/" in source_url.lower():
        return False
    try:
        raw = path.read_bytes()
        if not raw.startswith(b"%PDF-") or len(raw) < 20 * 1024:
            return False
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "".join((page.extract_text() or "") for page in reader.pages[:3])
        compact = re.sub(r"\s+", "", text)
        placeholder = compact in {"1/1绘制", "1/1", "绘制"}
        if placeholder:
            return False
        if len(reader.pages) == 1 and len(raw) < 100 * 1024 and len(compact) < 200:
            return False
        return True
    except Exception:
        return False


async def _save_opened_document(context: Any, download_dir: Path, title: str) -> dict[str, Any] | None:
    """Persist an official PDF that CNKI opened in a tab instead of downloading."""
    pages = [candidate for candidate in context.pages if not candidate.is_closed()]
    for opened in reversed(pages):
        url = str(opened.url or "")
        if "/ads/" in url.lower():
            try:
                await opened.close()
            except Exception:
                pass
            continue
        if ".pdf" not in url.lower() and "/pdf/" not in url.lower():
            continue
        printed = download_dir / _safe_download_name(title)
        try:
            await opened.pdf(path=str(printed), print_background=True, prefer_css_page_size=True)
            raw = printed.read_bytes()
            if _is_article_pdf(printed, url):
                return {
                    "title": title or printed.stem,
                    "detail_url": url,
                    "button_label": "官方 PDF 打印保存",
                    "file_name": printed.name,
                    "file_path": str(printed),
                    "failure": None,
                }
        except Exception:
            printed.unlink(missing_ok=True)
        response = await opened.reload(wait_until="commit", timeout=45_000)
        if response is None or not response.ok:
            continue
        raw = await response.body()
        content_type = str(response.headers.get("content-type") or "").lower()
        if not raw.startswith(b"%PDF-") and "application/pdf" not in content_type:
            continue
        destination = download_dir / _safe_download_name(title)
        destination.write_bytes(raw)
        if not _is_article_pdf(destination, url):
            destination.unlink(missing_ok=True)
            continue
        return {
            "title": title or destination.stem,
            "detail_url": url,
            "button_label": "官方 PDF 阅读",
            "file_name": destination.name,
            "file_path": str(destination),
            "failure": None,
        }
    return None


async def search_cnki(page: Any, query: str, limit: int = 20) -> list[dict[str, Any]]:
    query = _compact(query)
    if not query:
        raise ValueError("知网检索主题不能为空")
    url = CNKI_SEARCH_URL.format(query=urllib.parse.quote(query))
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass
    try:
        await page.locator(CNKI_RESULT_SELECTOR).first.wait_for(state="attached", timeout=12_000)
    except Exception:
        pass
    results = await page.locator(CNKI_RESULT_SELECTOR).evaluate_all(
        """(links) => links.slice(0, 160).map((link) => ({
            title: (link.getAttribute('title') || link.textContent || '').trim(),
            url: link.href || '',
            container: (link.closest('tr, li, .result-table-list, .search-result-item, .result-item, .result')?.innerText || '').trim()
        }))"""
    )
    unique: dict[str, dict[str, Any]] = {}
    for item in results:
        title = _compact(str(item.get("title") or ""))
        detail_url = str(item.get("url") or "")
        if not title or len(title) < 3 or not detail_url or not _looks_like_result_url(detail_url):
            continue
        container = _compact(str(item.get("container") or ""))
        year_match = re.search(r"(?:19|20)\d{2}", container)
        unique.setdefault(
            detail_url,
            {
                "title": title,
                "detail_url": detail_url,
                "summary": container[:500],
                "year": year_match.group(0) if year_match else "",
                "source": "cnki",
            },
        )
        if len(unique) >= limit:
            break
    if unique:
        return list(unique.values())

    diagnostics = await _empty_result_diagnostics(page, query)
    if diagnostics["explicit_empty"]:
        return []
    if diagnostics["challenge"]:
        raise RuntimeError(f"知网触发了安全验证，请在机构浏览器完成验证码后重试。诊断：{diagnostics}")
    if diagnostics["login_required"]:
        raise RuntimeError(f"知网登录状态未生效，请在机构浏览器重新登录后重试。诊断：{diagnostics}")
    raise RuntimeError(
        "知网页面已打开，但未能解析论文结果；可能是页面结构或 WebVPN 链接已变化。"
        f"诊断：{diagnostics}"
    )


async def download_cnki_result(
    context: Any,
    item: dict[str, Any],
    download_dir: Path,
) -> dict[str, Any]:
    page = await context.new_page()
    try:
        await page.goto(item["detail_url"], wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        candidates = page.locator(
            'a:has-text("PDF下载"), a:has-text("CAJ下载"), '
            'button:has-text("PDF下载"), button:has-text("CAJ下载"), '
            'a[href*="download" i], a[href*="Download"], a[href$=".pdf" i], a[href$=".caj" i]'
        )
        count = await candidates.count()
        diagnostics = await candidates.evaluate_all(
            """(nodes) => nodes.slice(0, 40).map((node, index) => ({
                index,
                text: (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                href: (node.getAttribute('href') || '').split('?')[0].slice(0, 180),
                tag: node.tagName,
                visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length)
            }))"""
        )
        if count == 0:
            body_text = _compact(await page.locator("body").inner_text())[:1000]
            raise RuntimeError(
                "当前详情页没有发现官方 PDF/CAJ 下载入口。"
                f"页面状态：{body_text[:240]}"
            )
        ordered = sorted(
            [item for item in diagnostics if item.get("visible") and "/ads/" not in str(item.get("href") or "").lower()],
            key=_download_candidate_rank,
        )[:6]
        for descriptor in ordered:
            index = int(descriptor["index"])
            candidate = candidates.nth(index)
            if not await candidate.is_visible():
                continue
            label = _compact(await candidate.inner_text())
            try:
                async with page.expect_download(timeout=8_000) as download_info:
                    await candidate.click()
                download = await download_info.value
                suggested = download.suggested_filename or f"cnki-{index + 1}.bin"
                destination = download_dir / suggested
                await download.save_as(str(destination))
                suffix = destination.suffix.lower()
                if suffix == ".pdf" and not _is_article_pdf(destination, str(page.url or "")):
                    destination.unlink(missing_ok=True)
                    continue
                return {
                    "title": item.get("title") or suggested,
                    "detail_url": item["detail_url"],
                    "button_label": label,
                    "file_name": destination.name,
                    "file_path": str(destination),
                    "failure": await download.failure(),
                }
            except Exception:
                opened = await _save_opened_document(
                    context, download_dir, str(item.get("title") or "cnki-paper")
                )
                if opened:
                    return opened
                continue
        raise RuntimeError(
            "发现了下载入口，但没有获得可校验的论文 PDF/CAJ；"
            f"候选入口：{diagnostics}"
        )
    finally:
        await page.close()
