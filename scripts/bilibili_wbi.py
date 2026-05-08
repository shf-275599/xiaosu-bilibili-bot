#!/usr/bin/env python3
"""Bilibili WBI 签名模块 + AI 摘要获取工具。

用法:
  python bilibili_wbi.py <BVID> [cookies_file]
  python bilibili_wbi.py --user-info <MID> [cookies_file]
  python bilibili_wbi.py --search-user <keyword> [cookies_file]
  python bilibili_wbi.py BV1ae411R7Ez
  python bilibili_wbi.py --user-info 363098992
  python bilibili_wbi.py --search-user lovaisy

依赖: pip install requests
"""

import hashlib
import urllib.parse
import time
import json
import sys
import os
import random
import requests


# ─── WBI 签名实现 ───────────────────────────────────────────────

mixin_key_enc_tab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def get_mixin_key(orig: str) -> str:
    """从原始密钥字符串提取 32 位 mixin key"""
    return ''.join(orig[n] for n in mixin_key_enc_tab)[:32]


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """对请求参数进行 WBI 签名"""
    mixin_key = get_mixin_key(img_key + sub_key)
    curr_time = round(time.time())
    params['wts'] = curr_time
    params = dict(sorted(params.items()))
    params = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params['w_rid'] = wbi_sign
    return params


def parse_cookies_file(filepath: str) -> dict:
    """解析 Netscape 格式的 cookies 文件"""
    cookies = {}
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('#') or not line:
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                name = parts[5]
                value = parts[6]
                cookies[name] = urllib.parse.unquote(value)
    return cookies


# ─── 视频 AI 摘要 ───────────────────────────────────────────────

def get_video_ai_summary(bvid: str, cid: int, up_mid: int, cookies_file: str) -> dict:
    """获取视频 AI 摘要和字幕"""
    cookies = parse_cookies_file(cookies_file)
    sessdata = cookies.get('SESSDATA', '')
    bili_jct = cookies.get('bili_jct', '')

    cookie_str = f'SESSDATA={sessdata}; bili_jct={bili_jct}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://www.bilibili.com/video/{bvid}',
        'Cookie': cookie_str,
    }

    # Step 1: 获取 WBI 签名密钥
    nav_resp = requests.get('https://api.bilibili.com/x/web-interface/nav', headers=headers)
    nav_data = nav_resp.json()

    if nav_data.get('code') != 0:
        return {'error': f"获取 WBI 密钥失败: {nav_data.get('message')}"}

    wbi_img = nav_data['data']['wbi_img']
    img_key = wbi_img['img_url'].split('/')[-1].split('.')[0]
    sub_key = wbi_img['sub_url'].split('/')[-1].split('.')[0]

    # Step 2: 调用 AI 摘要 API
    params = {'bvid': bvid, 'cid': cid, 'up_mid': up_mid}
    signed_params = enc_wbi(params, img_key, sub_key)

    resp = requests.get(
        'https://api.bilibili.com/x/web-interface/view/conclusion/get',
        params=signed_params,
        headers=headers,
    )

    return resp.json()


# ─── 用户信息 ───────────────────────────────────────────────────

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
]


def get_user_info(mid: int, cookies_file: str, max_retries: int = 5) -> dict:
    """获取用户信息（需要 WBI 签名，带反风控措施）"""
    cookies = parse_cookies_file(cookies_file)
    sessdata = cookies.get('SESSDATA', '')
    bili_jct = cookies.get('bili_jct', '')

    cookie_str = f'SESSDATA={sessdata}; bili_jct={bili_jct}'
    
    for attempt in range(max_retries):
        # 随机延迟 2-5 秒（首次请求也延迟）
        delay = random.uniform(2.0, 5.0)
        time.sleep(delay)
        
        # 随机 User-Agent
        ua = random.choice(USER_AGENTS)
        
        headers = {
            'User-Agent': ua,
            'Referer': f'https://space.bilibili.com/{mid}',
            'Cookie': cookie_str,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Origin': 'https://space.bilibili.com',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
        }

        # Step 1: 获取 WBI 签名密钥
        nav_resp = requests.get('https://api.bilibili.com/x/web-interface/nav', headers=headers)
        nav_data = nav_resp.json()

        if nav_data.get('code') != 0:
            if attempt < max_retries - 1:
                continue
            return {'error': f"获取 WBI 密钥失败: {nav_data.get('message')}"}

        wbi_img = nav_data['data']['wbi_img']
        img_key = wbi_img['img_url'].split('/')[-1].split('.')[0]
        sub_key = wbi_img['sub_url'].split('/')[-1].split('.')[0]

        # Step 2: 调用用户信息 API
        params = {'mid': mid}
        signed_params = enc_wbi(params, img_key, sub_key)

        resp = requests.get(
            'https://api.bilibili.com/x/space/wbi/acc/info',
            params=signed_params,
            headers=headers,
        )
        
        result = resp.json()
        
        # 如果成功或非风控错误，直接返回
        if result.get('code') == 0 or result.get('code') != -352:
            return result
        
        # 风控失败，等待更长时间后重试
        if attempt < max_retries - 1:
            wait_time = random.uniform(5.0, 10.0)
            time.sleep(wait_time)
    
    return result


# ─── 用户搜索 ───────────────────────────────────────────────────

def search_user(keyword: str, cookies_file: str, max_retries: int = 3) -> dict:
    """搜索用户（需要 WBI 签名，带反风控措施）"""
    cookies = parse_cookies_file(cookies_file)
    sessdata = cookies.get('SESSDATA', '')
    bili_jct = cookies.get('bili_jct', '')

    cookie_str = f'SESSDATA={sessdata}; bili_jct={bili_jct}'
    
    for attempt in range(max_retries):
        # 随机延迟 2-4 秒
        delay = random.uniform(2.0, 4.0)
        time.sleep(delay)
        
        # 随机 User-Agent
        ua = random.choice(USER_AGENTS)
        
        headers = {
            'User-Agent': ua,
            'Referer': 'https://search.bilibili.com',
            'Cookie': cookie_str,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Origin': 'https://search.bilibili.com',
        }

        # Step 1: 获取 WBI 签名密钥
        nav_resp = requests.get('https://api.bilibili.com/x/web-interface/nav', headers=headers)
        nav_data = nav_resp.json()

        if nav_data.get('code') != 0:
            if attempt < max_retries - 1:
                continue
            return {'error': f"获取 WBI 密钥失败: {nav_data.get('message')}"}

        wbi_img = nav_data['data']['wbi_img']
        img_key = wbi_img['img_url'].split('/')[-1].split('.')[0]
        sub_key = wbi_img['sub_url'].split('/')[-1].split('.')[0]

        # Step 2: 调用搜索 API
        params = {
            'search_type': 'bili_user',
            'keyword': keyword,
            'page': 1,
            'order': 0,
            'duration': 0,
            'tids': 0,
        }
        signed_params = enc_wbi(params, img_key, sub_key)

        resp = requests.get(
            'https://api.bilibili.com/x/web-interface/search/type',
            params=signed_params,
            headers=headers,
        )
        
        result = resp.json()
        
        # 如果成功或非风控错误，直接返回
        if result.get('code') == 0 or result.get('code') != -352:
            return result
        
        # 风控失败，等待更长时间后重试
        if attempt < max_retries - 1:
            wait_time = random.uniform(5.0, 10.0)
            time.sleep(wait_time)
    
    return result


# ─── CLI 入口 ───────────────────────────────────────────────────

DEFAULT_COOKIES = "/home/shf/bilibili-bot/config/bilibili-cookies.txt"


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <BVID> [cookies_file]", file=sys.stderr)
        print(f"      {sys.argv[0]} --user-info <MID> [cookies_file]", file=sys.stderr)
        print(f"      {sys.argv[0]} --search-user <keyword> [cookies_file]", file=sys.stderr)
        print(f"示例: {sys.argv[0]} BV1ae411R7Ez", file=sys.stderr)
        print(f"      {sys.argv[0]} --user-info 363098992", file=sys.stderr)
        print(f"      {sys.argv[0]} --search-user lovaisy", file=sys.stderr)
        sys.exit(1)

    # 处理 --user-info 参数
    if sys.argv[1] == '--user-info':
        if len(sys.argv) < 3:
            print("错误: --user-info 需要提供 MID 参数", file=sys.stderr)
            sys.exit(1)
        
        mid = int(sys.argv[2])
        cookies_file = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_COOKIES
        
        result = get_user_info(mid, cookies_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 处理 --search-user 参数
    if sys.argv[1] == '--search-user':
        if len(sys.argv) < 3:
            print("错误: --search-user 需要提供关键词参数", file=sys.stderr)
            sys.exit(1)
        
        keyword = sys.argv[2]
        cookies_file = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_COOKIES
        
        # 优先尝试浏览器工作流
        import subprocess
        script_dir = os.path.dirname(os.path.abspath(__file__))
        browser_script = os.path.join(script_dir, 'bilibili-search-user-browser.js')
        try:
            result = subprocess.run(
                ['node', browser_script, keyword, cookies_file],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                print(result.stdout)
                return
            else:
                print(f"[回退] 浏览器搜索失败: {result.stderr.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"[回退] 浏览器搜索异常: {e}", file=sys.stderr)
        
        # 回退到 Python API 方案
        result = search_user(keyword, cookies_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 处理视频 AI 摘要
    bvid = sys.argv[1]
    cookies_file = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_COOKIES

    # 构建带 cookie 的 headers
    cookies = parse_cookies_file(cookies_file)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://www.bilibili.com/video/{bvid}',
        'Cookie': f"SESSDATA={cookies.get('SESSDATA', '')}; bili_jct={cookies.get('bili_jct', '')}",
    }

    # 获取视频信息（提取 cid 和 up_mid）
    video_info = requests.get(
        'https://api.bilibili.com/x/web-interface/view',
        params={'bvid': bvid},
        headers=headers,
    ).json()

    if video_info.get('code') != 0:
        print(f"获取视频信息失败: {video_info.get('message')}", file=sys.stderr)
        sys.exit(1)

    cid = video_info['data']['cid']
    up_mid = video_info['data']['owner']['mid']

    # 获取 AI 摘要
    result = get_video_ai_summary(bvid, cid, up_mid, cookies_file)

    if result.get('code') == 0:
        data = result['data']
        data_code = data.get('code', -1)
        
        # 检查 AI 摘要是否可用
        if data_code == -1:
            print("AI 摘要不可用：该视频不支持 AI 摘要功能", file=sys.stderr)
            print("建议使用 Whisper 语音转录作为替代方案", file=sys.stderr)
            sys.exit(2)
        elif data_code == 1:
            print("AI 摘要排队中：视频正在生成摘要，请稍后重试", file=sys.stderr)
            print("建议使用 Whisper 语音转录作为替代方案", file=sys.stderr)
            sys.exit(3)
        
        model_result = data.get('model_result', {})

        # AI 摘要
        summary = model_result.get('summary')
        if summary:
            print("=== AI 摘要 ===")
            print(summary)
            print()
        else:
            print("AI 摘要为空：视频可能不支持摘要或摘要生成失败", file=sys.stderr)

        # 大纲（带时间戳）
        outline = model_result.get('outline', [])
        if outline:
            print("=== 大纲 ===")
            for section in outline:
                print(f"[{section['timestamp']}s] {section['title']}")
                for part in section.get('part_outline', []):
                    print(f"  [{part['timestamp']}s] {part['content']}")
            print()

        # 完整字幕
        subtitle = model_result.get('subtitle', [])
        if subtitle:
            print("=== 字幕 ===")
            for section in subtitle:
                for part in section.get('part_subtitle', []):
                    print(f"[{part['start_timestamp']}s -> {part['end_timestamp']}s] {part['content']}")
    else:
        error_msg = result.get('message', '未知错误')
        print(f"AI 摘要请求失败：{error_msg}", file=sys.stderr)
        print("建议使用 Whisper 语音转录作为替代方案", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
