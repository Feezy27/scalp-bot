# Backtest ScalpRadar — resultats

Periode d'apprentissage : 2025-07-01 → 2026-04-01 (9.0 mois)  
Periode de TEST (jamais vue) : 2026-04-01 → 2026-10-22 (6.7 mois)  
Risque 2 % par trade, frais + glissement + funding inclus, 1 position a la fois.  
Reference : BTC achat-conservation sur la periode de test = +19.5 %

## Periode de test (le seul chiffre qui compte)

| Strategie | Trades | Trades/mois | Gagnants | Esperance (R) | Profit factor | Rendement | Pire baisse |
|---|---|---|---|---|---|---|---|
| actuelle | 0 | 0.0 | 0 % | +0.00 | 0.00 | +0.0 % | 0.0 % |
| pullback | 52 | 7.7 | 35 % | -0.14 | 0.80 | -14.9 % | 19.2 % |
| breakout | 38 | 5.6 | 26 % | -0.21 | 0.68 | -15.6 % | 22.1 % |
| range | 145 | 21.5 | 40 % | -0.09 | 0.86 | -27.4 % | 38.9 % |
| regime | 150 | 22.3 | 36 % | -0.17 | 0.76 | -42.3 % | 48.1 % |
| aleatoire | 83 | 12.3 | 37 % | -0.12 | 0.84 | -20.1 % | 27.3 % |

## Periode d'apprentissage

| Strategie | Parametres retenus | Trades | Gagnants | Esperance (R) | Profit factor | Rendement |
|---|---|---|---|---|---|---|
| actuelle | `{}` | 0 | 0 % | +0.00 | 0.00 | +0.0 % |
| pullback | `{'tp_r': 2.5, 'adx': 25}` | 69 | 33 % | +0.03 | 1.04 | +0.8 % |
| breakout | `{'adx': 25, 'trail': 3.5}` | 48 | 38 % | +0.28 | 1.50 | +26.9 % |
| range | `{'adx_max': 20}` | 143 | 36 % | -0.13 | 0.82 | -34.2 % |
| regime | `{'tp_r': 2.5, 'adx': 25, 'adx_max': 18}` | 171 | 32 % | -0.16 | 0.77 | -46.7 % |
| aleatoire | `{'seed': 1}` | 103 | 34 % | -0.21 | 0.73 | -37.4 % |

## Test par paire (stabilite)

- **actuelle** : BTC +0.00R/0 · ETH +0.00R/0 · SOL +0.00R/0 · BNB +0.00R/0 · XRP +0.00R/0 · LINK +0.00R/0 · AVAX +0.00R/0
- **pullback** : BTC +0.31R/10 · ETH +0.25R/22 · SOL -0.39R/20 · BNB -0.53R/10 · XRP +0.12R/18 · LINK -0.15R/9 · AVAX -0.32R/5
- **breakout** : BTC -0.04R/19 · ETH -0.24R/15 · SOL +0.07R/16 · BNB -0.58R/22 · XRP -0.08R/17 · LINK -0.19R/19 · AVAX -0.09R/15
- **range** : BTC -0.17R/37 · ETH -0.08R/48 · SOL -0.43R/24 · BNB -0.17R/44 · XRP -0.42R/35 · LINK -0.38R/35 · AVAX +0.21R/53
- **regime** : BTC -0.00R/35 · ETH -0.03R/59 · SOL -0.42R/36 · BNB -0.28R/45 · XRP -0.23R/46 · LINK -0.50R/34 · AVAX +0.02R/47
