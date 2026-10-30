# Results

All numbers are **ROI per $1 staked** at *executable* prices — the volume-weighted price of real
taker **buys** on Polymarket in the window right after the game-state snapshot — minus a conservative
fee of `0.05 · p · (1 − p)` per share. Intervals are 95% bootstrap CIs resampled **by game**
(bets inside one game are correlated). ✅ / ❌ = interval entirely above / below zero.

> Lesson learned early: `prices-history` on illiquid in-play markets returns the *midpoint of an empty
> book*. A first pass on those prices showed a fake +14% edge on NBA spreads; it vanished on real trades.

## NBA — halftime (1 311 games, 2025-26 + start of 2026-27)

| Bet | Halftime gap | Market price | Actual win rate | ROI | 95% CI |
|---|---|---|---|---|---|
| Moneyline, **trailing** team | 10–14 | 20.3% | 16.7% | **−25.4%** | [−48.1; −1.5] ❌ |
| Moneyline, leading team | 10–14 | 80.6% | 83.2% | +3.2% | [−3.1; +9.1] |
| Spread, leading team (1 bet/game) | 10+ | 78.2% | 81.6% | +4.1% | [−2.4; +10.3] |

Calibration is tight from 0.3 to 1.0; cheap outcomes (≤ 0.2) lose ~22% — classic favourite-longshot bias.

## NHL — after the 2nd period (1 540 games)

| Bet | Situation | ROI | 95% CI |
|---|---|---|---|
| Moneyline, leader | up 1 / 2 / 3+ | −0.1% / +0.6% / +0.5% | all contain 0 |
| Moneyline, trailing | down 1 | **−16.5%** | [−29.9; −2.8] ❌ |
| Moneyline, trailing, even pre-game | down 1+ | **−25.7%** | [−41.1; −9.9] ❌ |

## Soccer — halftime (2 245 games: EPL, LaLiga, Serie A, Bundesliga, Ligue 1, UCL)

The most efficient market tested. No segment with a positive interval; "leading by 1 → wins"
ranges −10% … +9% by league, all inside noise. Betting on a team trailing by 2+ to win: −49% (wide CI).

## Tennis — right after set 1 (2 346 matches, Slams + Masters/1000 + Finals)

| Player | Pre-match status | ROI | 95% CI |
|---|---|---|---|
| **Lost** set 1 | favourite (> 60%) | **−14.2%** | [−24.2; −4.5] ❌ |
| **Won** set 1 | underdog (< 40%) | **+8.0%** | [−1.9; +18.5] |
| Won set 1 | even / favourite | +0.7% / −0.7% | contain 0 |

Two sides of the same bias: after a favourite drops the first set the crowd keeps paying for the comeback,
so the underdog who took the set is under-priced. This is the only candidate strategy, now running in paper mode.

## Takeaways

1. **Buying comebacks loses money in every sport tested** — the most robust result here.
2. Leaders / favourites in-play are priced about right: ~0% ROI after costs.
3. One forward-test candidate: *tennis underdog who won set 1*. Everything else: no edge.
