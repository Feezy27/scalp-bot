# Backtest 2 — hypotheses figees, janvier 2022 → aujourd'hui

Aucun reglage ajuste sur ces donnees. Risque 2 % par trade, frais + glissement + funding inclus.  
Critere de validation : esperance positive au total (t-stat > 2) ET dans la majorite des semestres.

## Synthese

| Strategie | Trades | Esperance (R) | t-stat | Semestres positifs |
|---|---|---|---|---|
| turtle55 | 240 | +0.050 | +0.60 | 5/10 |
| squeeze | 436 | -0.089 | -1.36 | 3/10 |
| breakout_fixe | 333 | +0.044 | +0.50 | 6/10 |
| pullback_fixe | 463 | -0.094 | -1.42 | 2/10 |
| aleatoire2 | 664 | -0.218 | -4.25 | 0/10 |

## Esperance (R) par semestre — [nb de trades]

| Semestre | BTC achat-conserv. | turtle55 | squeeze | breakout_fixe | pullback_fixe | aleatoire2 |
|---|---|---|---|---|---|---|
| 2022-S1 | -59 % | +0.30 [21] | +0.01 [43] | +0.26 [28] | -0.06 [42] | -0.11 [66] |
| 2022-S2 | -13 % | +0.12 [27] | -0.22 [36] | -0.25 [39] | -0.19 [53] | -0.24 [61] |
| 2023-S1 | +84 % | -0.00 [30] | -0.13 [47] | +0.34 [32] | -0.07 [46] | -0.28 [73] |
| 2023-S2 | +39 % | +0.06 [21] | -0.31 [57] | +0.04 [40] | -0.29 [55] | -0.06 [61] |
| 2024-S1 | +49 % | -0.16 [27] | -0.13 [39] | -0.11 [38] | -0.03 [54] | -0.09 [68] |
| 2024-S2 | +49 % | +0.31 [25] | +0.16 [51] | +0.34 [35] | +0.03 [48] | -0.10 [65] |
| 2025-S1 | +15 % | -0.21 [29] | +0.21 [42] | -0.26 [35] | -0.11 [43] | -0.20 [73] |
| 2025-S2 | -18 % | -0.03 [27] | -0.17 [44] | +0.26 [36] | -0.07 [53] | -0.33 [81] |
| 2026-S1 | -33 % | +0.38 [19] | -0.15 [46] | +0.17 [27] | +0.02 [43] | -0.43 [69] |
| 2026-S2 | +39 % | -0.19 [14] | -0.16 [31] | -0.36 [23] | -0.12 [26] | -0.34 [47] |

## Par paire (toute la periode)

- **turtle55** : BTC +0.06R/154 · ETH +0.17R/152 · SOL +0.15R/155 · BNB +0.03R/165 · XRP -0.02R/165 · LINK +0.02R/155 · AVAX +0.00R/165
- **squeeze** : BTC -0.05R/232 · ETH -0.08R/219 · SOL +0.03R/211 · BNB -0.15R/214 · XRP +0.07R/206 · LINK -0.11R/207 · AVAX +0.02R/186
- **breakout_fixe** : BTC +0.17R/158 · ETH +0.16R/159 · SOL +0.02R/173 · BNB +0.05R/145 · XRP -0.02R/147 · LINK -0.07R/173 · AVAX +0.02R/167
- **pullback_fixe** : BTC +0.08R/109 · ETH +0.17R/127 · SOL +0.00R/131 · BNB -0.19R/86 · XRP -0.03R/113 · LINK +0.04R/131 · AVAX +0.00R/120
- **aleatoire2** : BTC -0.22R/663 · ETH -0.16R/681 · SOL -0.15R/673 · BNB -0.22R/672 · XRP -0.05R/659 · LINK -0.15R/676 · AVAX -0.15R/679
