import os

filepath = 'tests/ops/test_campaign.py'
with open(filepath, 'r') as f:
    content = f.read()

# Instead of using a pytest fixture, we can just define a helper function or dict at the top level
# so that the tests don't have to change their signatures or rely on pytest fixtures that may not be loaded properly if conftest is disabled

content = content.replace('def test_all_pass(self, base_campaign_metrics):', 'def test_all_pass(self):')
content = content.replace('def test_seed_data_blocks(self, base_campaign_metrics):', 'def test_seed_data_blocks(self):')
content = content.replace('def test_recon_failure_blocks(self, base_campaign_metrics):', 'def test_recon_failure_blocks(self):')
content = content.replace('def test_high_drawdown_blocks(self, base_campaign_metrics):', 'def test_high_drawdown_blocks(self):')
content = content.replace('def test_negative_expectancy_blocks(self, base_campaign_metrics):', 'def test_negative_expectancy_blocks(self):')
content = content.replace('def test_promotion_trade_count_can_fail_while_total_high(self, base_campaign_metrics):', 'def test_promotion_trade_count_can_fail_while_total_high(self):')

if 'base_campaign_metrics =' not in content:
    content = content.replace('class TestCampaignValidationGates:', 'base_campaign_metrics = dict(campaign_days=7, total_trades=100, all_recon_clean=True, max_dd_observed=0.01, avg_latency_p95_ms=100, stale_ratio=0.0, reject_ratio=0.0, error_count=0, expectancy=1.0, seed_trade_count=0)\n\nclass TestCampaignValidationGates:')

with open(filepath, 'w') as f:
    f.write(content)
