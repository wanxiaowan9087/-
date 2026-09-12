"""采集国内厂商公开的中文扫地机器人资料（仅保存待审核候选）。

脚本遵守 robots.txt、只访问显式来源和站点地图中的公开页面，并限制请求频率。
它不会绕过登录、验证码、付费墙或反爬措施，也不会自动写入 ZENMOP 的 pgvector。
下载结果放在“国内官方资料待审核”目录，人工核验后才能转换为生产知识库清单。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path


# 只允许国内中文官网；新增来源前先确认其公开、中文、官方属性。
SOURCES = {
    "石头科技": {
        "hosts": {"roborock.com.cn", "www.roborock.com.cn"},
        "sitemaps": ("https://www.roborock.com.cn/sitemap.xml", "https://www.roborock.com.cn/sitemap_index.xml"),
    },
    "科沃斯": {
        "hosts": {"ecovacs.com.cn", "www.ecovacs.com.cn"},
        "sitemaps": ("https://www.ecovacs.com.cn/sitemap.xml", "https://www.ecovacs.com.cn/sitemap_index.xml"),
    },
    "追觅科技": {
        "hosts": {"dreame.tech", "www.dreame.tech", "dreame.com.cn", "www.dreame.com.cn"},
        "sitemaps": ("https://www.dreame.tech/sitemap.xml", "https://www.dreame.com.cn/sitemap.xml"),
    },
    "小米": {
        "hosts": {"mi.com", "www.mi.com"},
        "sitemaps": ("https://www.mi.com/sitemap.xml",),
        "seeds": ("https://www.mi.com/roomrobot",),
    },
}
KEYWORDS = re.compile(r"扫地机器人|扫拖机器人|清洁机器人|使用说明|故障|排查|维护|保养|滤网|滚刷|边刷|充电|充电座|水箱|拖布|地图|清洁|售后|保修|型号")
ROBOT_TERMS = re.compile(r"扫地|扫拖|吸尘|机器人|拖地|滚刷|边刷|充电座|水箱|拖布|滤网")
USER_AGENT = "ZENMOP-ResearchBot/0.2 (+personal-study)"
TITLE_TAG = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
TRAILING_PAGE_CHROME = re.compile(r"(?is)\b(?:联系我们|隐私政策|用户协议|版权声明|关注我们|返回顶部|Copyright)\b.*$")


class VisibleTextParser(HTMLParser):
    """提取文章/主内容区域，避免把导航和页脚写进知识库。"""

    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside"}
    _BODY_MARKERS = {"article-body", "post-content", "text-content", "article-content", "detail-content", "main-content"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._content_depth = 0
        self._title: list[str] = []
        self._heading: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._parts: list[str] = []
        self._markers: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").lower()
        element_id = (attributes.get("id") or "").lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
            self._title.append("")
        if tag == "h1":
            self._in_h1 = True
            self._heading.append("")
        marker = tag in {"article", "main"} or any(token in classes or token in element_id for token in self._BODY_MARKERS)
        self._markers.append(marker)
        if marker:
            self._content_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if self._markers:
            if self._markers.pop() and self._content_depth:
                self._content_depth -= 1
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        text = " ".join(data.split())
        if self._in_title and self._title:
            self._title[-1] += f" {text}"
        if self._in_h1 and self._heading:
            self._heading[-1] += f" {text}"
        if self._content_depth:
            self._parts.append(text)

    @property
    def title(self) -> str:
        return next((x.strip() for x in self._heading if x.strip()), "") or next((x.strip() for x in self._title if x.strip()), "")

    @property
    def text(self) -> str:
        lines: list[str] = []
        previous = ""
        for part in self._parts:
            if part != previous:
                lines.append(part)
            previous = part
        return "\n".join(lines)


def fetch(url: str, *, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def load_robots(host: str) -> urllib.robotparser.RobotFileParser | None:
    robots = urllib.robotparser.RobotFileParser(f"https://{host}/robots.txt")
    try:
        robots.read()
    except Exception as error:
        _log(f"无法读取 robots.txt，跳过站点 {host}: {error}")
        return None
    return robots


def extract_title(raw_html: str, fallback_url: str) -> str:
    match = TITLE_TAG.search(raw_html)
    if match:
        title = " ".join(html.unescape(match.group(1)).split())
        title = re.split(r"\s+[–—|-]\s+", title, maxsplit=1)[0].strip()
        if title:
            return title
    return urllib.parse.unquote(fallback_url.rsplit("/", 1)[-1]) or "未命名资料"


def clean_article_text(text: str) -> str:
    text = TRAILING_PAGE_CHROME.sub("", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def chinese_ratio(text: str) -> float:
    meaningful = re.findall(r"[\u4e00-\u9fffA-Za-z]", text)
    if not meaningful:
        return 0.0
    return len(re.findall(r"[\u4e00-\u9fff]", text)) / len(meaningful)


def sitemap_entries(sitemap_url: str) -> list[tuple[str, str | None]]:
    raw = fetch(sitemap_url)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 部分国内站点的 sitemap 混入未转义字符，退回到只读取 loc/lastmod 标签。
        text = raw.decode("utf-8", errors="replace")
        locs = re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", text, re.I)
        dates = re.findall(r"<lastmod>\s*([^<\s]+)\s*</lastmod>", text, re.I)
        return [(loc, dates[index] if index < len(dates) else None) for index, loc in enumerate(locs)]
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries: list[tuple[str, str | None]] = []
    for node in root.findall("sm:url", ns):
        loc = node.findtext("sm:loc", namespaces=ns)
        if loc:
            entries.append((loc.strip(), node.findtext("sm:lastmod", namespaces=ns)))
    for node in root.findall("sm:sitemap", ns):
        loc = node.findtext("sm:loc", namespaces=ns)
        if loc:
            try:
                entries.extend(sitemap_entries(loc.strip()))
            except Exception as error:
                _log(f"跳过子站点地图 {loc}: {error}")
    return entries


def safe_name(title: str, source_name: str) -> str:
    title = re.sub(r"[\\/:*?\"<>|]", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .-")
    return f"{source_name}_{(title or '公开使用资料')[:100]}.txt"


def collect(source_names: list[str], limit: int, delay: float, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "manifest.jsonl"
    fetch_log = output / "采集日志.jsonl"
    records_by_source = _load_existing_manifest(manifest)
    seen: set[str] = set(records_by_source)
    with fetch_log.open("a", encoding="utf-8") as log:
        for source_name in source_names:
            config = SOURCES[source_name]
            host = sorted(config["hosts"])[0]
            robots = load_robots(host)
            if robots is None:
                _write_log(log, source_name, f"https://{host}/robots.txt", "站点不可访问", "无法读取 robots.txt")
                _log(f"跳过 {source_name}: robots.txt 不可访问")
                continue
            entries: list[tuple[str, str | None]] = []
            for sitemap in config["sitemaps"]:
                try:
                    entries = sitemap_entries(sitemap)
                    if entries:
                        break
                except Exception as error:
                    _log(f"站点地图不可用 {sitemap}: {error}")
            if not entries:
                _write_log(log, source_name, config["sitemaps"][0], "站点地图不可用", "没有可访问的站点地图")
                _log(f"跳过 {source_name}: 没有可访问的站点地图")
                continue
            seeds = [(url, None) for url in config.get("seeds", ())]
            candidates = [(url, lastmod) for url, lastmod in entries if urllib.parse.urlparse(url).hostname in config["hosts"] and KEYWORDS.search(urllib.parse.unquote(url)) and url not in seen]
            candidates.extend((url, lastmod) for url, lastmod in seeds if url not in seen)
            # 有些官网用数字/哈希路由，网址本身没有中文关键词；仅在这种情况下
            # 从同一官方 sitemap 取少量页面，再由正文中文比例和主题词过滤。
            if not candidates:
                candidates = [(url, lastmod) for url, lastmod in entries[:50] if urllib.parse.urlparse(url).hostname in config["hosts"] and url not in seen]
                _log(f"{source_name}: URL 无主题词，改用前 {len(candidates)} 个公开页面做正文筛选")
            candidates.sort(key=lambda item: item[1] or "", reverse=True)
            fetched_for_source = 0
            for url, lastmod in candidates:
                if fetched_for_source >= limit:
                    break
                seen.add(url)
                status = "待审核"
                try:
                    if not robots.can_fetch(USER_AGENT, url):
                        status = "robots 禁止"
                        raise RuntimeError(status)
                    raw_html = fetch(url).decode("utf-8", errors="replace")
                    parser = VisibleTextParser()
                    parser.feed(raw_html)
                    content = clean_article_text(parser.text)
                    title = parser.title or extract_title(raw_html, url)
                    if len(content) < 240:
                        status = "正文过短"
                        raise RuntimeError(status)
                    if chinese_ratio(f"{title}\n{content}") < 0.45:
                        status = "非中文内容"
                        raise RuntimeError(status)
                    # 只接受标题明确指向机器人/清洁部件的页面，避免官网通用页面
                    # 因页脚导航中的偶然词命中而被误收。
                    if not ROBOT_TERMS.search(title):
                        status = "与扫地机器人主题无关"
                        raise RuntimeError(status)
                    filename = safe_name(title, source_name)
                    path = output / filename
                    if path.exists():
                        status = "同名文件已存在"
                        raise RuntimeError(status)
                    path.write_text(content + "\n", encoding="utf-8")
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    records_by_source[url] = {"source": url, "title": title, "document_type": "text", "version": (lastmod or "").split("T", 1)[0] or None, "content_path": str(path), "sha256": digest, "retrieved_at": datetime.now(UTC).isoformat(), "review_status": "待审核", "rights_note": "国内官网公开中文资料，仅供个人学习和评测；发布或商用前需核对版权与转载条款。"}
                    fetched_for_source += 1
                    _log(f"已保存 {source_name}: {title} -> {filename}")
                    _write_log(log, source_name, url, status, title)
                    if fetched_for_source < limit:
                        time.sleep(delay)
                except Exception as error:
                    _log(f"跳过 {source_name}: {url}（{status}: {error}）")
                    _write_log(log, source_name, url, status, str(error))
            if fetched_for_source == 0 and candidates:
                _write_log(log, source_name, "", "本轮无合格中文资料", "候选页面均被正文长度、中文比例或主题词规则过滤")
    with manifest.open("w", encoding="utf-8") as stream:
        for record in records_by_source.values():
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    _log(f"采集完成：{len(records_by_source)} 篇；待审核清单：{manifest}")
    return len(records_by_source)


def _write_log(stream, source: str, url: str, status: str, detail: str) -> None:
    stream.write(json.dumps({"source_name": source, "url": url, "status": status, "detail": detail}, ensure_ascii=False) + "\n")


def _load_existing_manifest(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            source = record.get("source") if isinstance(record, dict) else None
            if isinstance(source, str) and source:
                records[source] = record
        except json.JSONDecodeError:
            continue
    return records


def _log(message: str) -> None:
    print(message.encode("ascii", errors="backslashreplace").decode("ascii"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=sorted(SOURCES), action="append")
    parser.add_argument("--limit-per-source", type=int, default=3)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("data/国内官方资料待审核"))
    args = parser.parse_args()
    if args.limit_per_source < 1 or args.delay_seconds < 0:
        parser.error("limit-per-source 必须为正数，delay-seconds 不能为负数")
    collect(args.source or sorted(SOURCES), args.limit_per_source, args.delay_seconds, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
