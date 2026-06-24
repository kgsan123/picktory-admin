"""Tving __NEXT_DATA__의 모든 band 목록 + label 필드 구조 분석."""
import re, json
from playwright.sync_api import sync_playwright

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(user_agent=UA)
    page.goto('https://www.tving.com/ranking/content', timeout=40000)
    page.wait_for_load_state('domcontentloaded', timeout=30000)
    page.wait_for_timeout(3000)
    html = page.content()
    browser.close()

m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
data = json.loads(m.group(1))
bands = data['props']['pageProps']['boardMainData']['bands']

out = {}
for band in bands:
    bt = band.get('bandType', '')
    bname = band.get('bandName', '')
    items = band.get('items', [])
    # 첫 3개 아이템의 label 필드 확인
    sample = []
    for item in items[:3]:
        sample.append({
            'title': item.get('title', ''),
            'label': item.get('label'),
            'keys': list(item.keys()),
        })
    out[bt] = {'bandName': bname, 'count': len(items), 'sample': sample}

with open('_tving_bands.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print('Saved to _tving_bands.json')
print('Band types:', list(out.keys()))
