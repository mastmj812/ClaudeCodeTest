# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Single-file Tic Tac Toe web app — all HTML, CSS, and JavaScript lives in `tictactoe.html`.

## Running

Open `tictactoe.html` directly in any browser. No build step, server, or dependencies required.

## Git Workflow

After completing any meaningful unit of work, commit and push to GitHub:

```
git add <files>
git commit -m "short, imperative summary of what changed"
git push
```

- Commit after each logical change (feature added, bug fixed, refactor done) — not per file save
- Keep commit messages concise and imperative: `add CPU difficulty selector`, `fix draw detection bug`
- Never let significant work sit uncommitted; push frequently so nothing is lost

## Architecture

All game logic is in a `<script>` block at the bottom of `tictactoe.html`.

**State**
- `board` — 9-element array of `''`, `'X'`, or `'O'`
- `current` — whose turn it is (`'X'` or `'O'`)
- `gameOver` — boolean
- `vsCPU` — boolean toggled by the mode button
- `scores` — `{ X, O, D }` persisted across games

**Key functions**
- `newGame()` — resets board, current, gameOver; does not clear scores
- `onCellClick(i)` — entry point for human moves; guards against taken cells and CPU's turn
- `afterMove()` — checks win/draw after every move, advances turn, triggers `cpuMove()` if needed
- `cpuMove()` — picks a move after a 380ms delay
- `pickMove()` — CPU strategy: win > block > center > corners > sides
- `checkWin(b, p)` — returns the winning line array or `null`
- `place(i, player)` — writes to `board` and updates the DOM cell
