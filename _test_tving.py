import logging, json
logging.basicConfig(level=logging.INFO)
from data_collector.show_discovery_ott import scan_tving
results = scan_tving()
with open('_tving_result.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f'Total: {len(results)}')
for r in results:
    ep = f" ({r['latest_episode']}화)" if r.get('latest_episode') else ''
    print(f"  [{r['category']:10}] {r['name']}{ep}  / {r['channel']}")
