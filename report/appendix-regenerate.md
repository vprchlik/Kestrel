# Appendix — numbers that must be regenerated

Seeded from `docs/AUDIT-2026-08.md` findings 16–23 at T4.3, before
prose could launder them. Disposition is regenerate / historical-only /
structural. Nothing in the historical-only column may appear as a
result.

| finding | claim | disposition |
|---|---|---|
| 16 | `PLAN.md` M2/M3 status rows stale at audit time | **historical-only** — fixed in the T4.0 PLAN edit; not a number |
| 17 | `src/main.rs` module doc still said M2 EXECUTION OK | **historical-only** — cosmetic; T4.3b |
| 18 | D-0026 `__heap_end` `0x8031_8000` not 2 MiB-aligned | **regenerate** — re-derive alignment at the superpage rung, do not quote the M1 address |
| 19 | D-0038 pool size "≈ 64 KiB" | **regenerate** — actual ≈ 49 KiB; fix the entry when the comparison section cites pool size |
| 20 | "~150 ms debug paging" | **historical-only** — labeled not-the-cost-of-paging; must not enter a results table |
| 21 | "E2 offset = 0"; OpenSBI "Firmware Size 322 KB" | **regenerate** under the pinned M4 QEMU (`just measure-e2`); offset is meaningless for `-bios none` |
| 22 | chat-only headline budget (OpenSBI ~24 ms, E2→E3g 42.5 ms, …) | **historical-only** — replaced wholesale by `baseline-t4.3` |
| 23 | 88 KiB/slot reservation; 1 MiB heap; RTO 200 ms; 69 frames = 67+2 | **structural** — may carry as-is until a rung breaks them (finding 24 at superpages) |

Finding 22 is why this appendix exists: a single-run chat budget is
not a result. The generated exhibits are.
