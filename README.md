# ScalpRadar V2 — bot de trading automatique BingX

Tourne gratuitement sur GitHub Actions toutes les 15 minutes. Aucun serveur, aucun abonnement.

## Securite
- Les 4 cles vivent **uniquement** dans les GitHub Secrets (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `BINGX_API_KEY`, `BINGX_API_SECRET`). Aucune n'est dans le code.
- La cle BingX doit avoir **uniquement** « Perpetual Futures / Trade ». **Jamais** « Withdraw » (retrait). Le code ne contient aucune fonction de retrait.
- L'etat du bot (capital, position) est **chiffre** (`etat.chiffre`) : illisible et infalsifiable sans ta cle secrete.
- Seul **ton** compte Telegram peut donner des ordres au bot. Les messages d'inconnus sont ignores.
- Les journaux publics ne montrent ni cles, ni montants.
- Actions GitHub et bibliotheques verrouillees par empreinte (impossible de les remplacer en douce).
- Avant chaque passage, 46 tests de securite tournent ; si un seul echoue, le bot ne trade pas.

## Garde-fous
| Regle | Valeur |
|---|---|
| Perte max par trade (SL touche, frais compris) | 2 % du capital (≈ 0,92 USDT pour 46 USDT) |
| Positions simultanees | 1 |
| Taille max | 3 × le capital, marge isolee ≤ 50 % du capital, levier ≤ 10 |
| Liquidation | au moins 3 × plus loin que le SL, verifiee apres ouverture |
| SL | pose chez BingX et **verifie** ; s'il manque : repose, sinon position fermee |
| Trades par jour | 3 max |
| Pause | apres 2 pertes de suite, ou -5 % sur la journee (jusqu'au lendemain) |
| Coupe-circuit | -25 % depuis le depart → arret total, seul `/reset_securite` relance |

## Commandes Telegram
`/status` · `/stop` · `/go` · `/fermer` (ferme la position) · `/reset_securite` · `/aide`

## Modes
Dans `config.py` : `MODE = "demo"` (argent fictif BingX, memes cles) ou `MODE = "reel"`.
Rester en demo plusieurs semaines avant de passer en reel.

## Backtest
`backtest/` — teste les strategies sur ~16 mois de donnees reelles (frais, glissement, financement compris),
choix des reglages sur une periode, verification sur une autre jamais vue. Resultats : `backtest/resultats_backtest.md`.
