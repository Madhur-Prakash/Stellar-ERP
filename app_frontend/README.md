<div align="center">

# Stellar ERP - desktop client

**The same product as the web app, as a native window.** Same screens, same design tokens,
same backend. Windows, macOS, and Linux from one codebase.

![Flutter](https://img.shields.io/badge/Flutter-3.44-02569B?style=flat-square&logo=flutter&logoColor=white)
![Dart](https://img.shields.io/badge/Dart-3.12-0175C2?style=flat-square&logo=dart&logoColor=white)
![Material](https://img.shields.io/badge/Material-3-757575?style=flat-square&logo=materialdesign&logoColor=white)
![Riverpod](https://img.shields.io/badge/Riverpod-state-4A90E2?style=flat-square)
![Platforms](https://img.shields.io/badge/platforms-Windows_macOS_Linux-6E7681?style=flat-square)

[Root README](../README.md) · [API](../docs/api.md) · [Architecture](../docs/architecture.md) · [Proof ledger](../docs/attestation.md) · [Commands](../docs/commands.md) · [Installer](../installer/README.md)

</div>

---

This is not a wrapper around the website. There is no embedded browser and no bundled web
build: it is a Flutter application that talks to the same FastAPI backend over the same
`/api/v1` surface, with its own rendering of the same design system.

---

## Running it

The backend has to be up first - this client has no server of its own.

```bash
make up          # from the repository root: PostgreSQL, Redis, the API
make desktop     # then the desktop client
```

`make desktop` picks the right target for the machine it runs on. To point it at a real
deployment:

```bash
make desktop API_BASE_URL=https://erp.example.com
```

A release binary:

```bash
make build-desktop                                  # this platform
make build-desktop API_BASE_URL=https://erp.example.com
```

The output lands in `build/<platform>/…/Release/`.

### Configuration

Three settings, read from `app_frontend/.env` at start-up by
[flutter_dotenv](https://pub.dev/packages/flutter_dotenv):

| Key | Default | Meaning |
| --- | --- | --- |
| `API_BASE_URL` | `http://127.0.0.1:8000` | Where the API lives |
| `API_V1_PREFIX` | `/api/v1` | The versioned path prefix |
| `APP_NAME` | `Stellar ERP` | Window title and footer |

```bash
cp .env.sample .env    # `make setup` does this for you
```

They are **validated on first read** and the app refuses to start on a bad one, for exactly
the reason the web app parses its own env with Zod - a missing base URL otherwise surfaces
much later as a request to `null/api/v1/auth/login`. A missing `.env` is not fatal: the
defaults above apply and a warning is printed, so a fresh checkout runs against localhost.

Two consequences of how flutter_dotenv works, both worth knowing:

- **`.env` is a bundled asset**, not a file read from disk - it loads through `rootBundle`.
  So it must exist before `flutter build` runs, or the build fails on a missing asset, and
  it is declared in `pubspec.yaml` as a single file rather than a directory because
  Flutter's asset bundler skips dotfiles when walking a directory entry.
- **It ships inside the application bundle.** Keep secrets out of it. These three values
  are a host, a path and a display name - all of which the client discloses in its first
  request anyway.

> **`127.0.0.1`, not `localhost`.** `docker compose` can publish the API on IPv4 only, and
> `localhost` on Windows resolves to `::1` first. A client that takes the first answer gets
> "connection refused" from a server that is running perfectly well - and because a failed
> session restore is indistinguishable from "not signed in", it presents as the sign-in
> screen with no explanation. Naming the address family removes the guess.

> **This replaced `--dart-define`, and fixed a Windows bug in the process.** The settings
> used to be compiled in, so changing a host meant a rebuild. Worse, Git Bash's MSYS layer
> rewrites arguments that look like Unix paths: `--dart-define=API_V1_PREFIX=/api/v1`
> reached the compiler as `C:/Program Files/Git/api/v1` and got baked into the binary, where
> the app's own validation caught it - correctly, but only after a two-minute build. A file
> has no argument parsing to survive.

---

## Quality gates

Folded into the root Makefile's targets, so they run with everything else:

```bash
make lint        # dart format --set-exit-if-changed
make typecheck   # flutter analyze
make test        # flutter test
```

Or directly:

```bash
flutter analyze
flutter test
dart format lib test
```

`flutter analyze` is the counterpart to the web app's `tsc -b` and its `eslint` at once -
Dart's analyzer does both jobs.

---

## Layout

```
lib/
├── main.dart          Start-up: cookie jar, stored theme, timezone database
├── app.dart           MaterialApp.router, both themes, the toast host
├── router.dart        Every route, and the whole auth boundary, in one file
├── theme/
│   ├── oklch.dart     OKLCH -> sRGB, so the tokens stay the CSS's own numbers
│   ├── tokens.dart    The design tokens, as a ThemeExtension
│   └── app_theme.dart Material 3 built *from* those tokens
├── core/              Env, HTTP client, error envelope, exact-decimal formatting,
│                      card-number checks (which keep no number)
├── models/            Hand-written mirrors of the backend contracts
├── api/               One thin typed binding per module
├── state/             Riverpod providers; the auth and theme controllers
├── widgets/           The design system: button, card, input, table, charts…
├── layout/            Sidebar, header, command palette, footer
└── features/          One directory per screen, mirroring frontend/src/features
    └── trust/         Ledger 3: sealing status, seal history, proof export
```

The tree deliberately mirrors `frontend/src`, so a change on one surface is easy to find on
the other.

---

## The three decisions worth knowing

Everything else is explained where it lives. These three shape the rest.

**The design tokens are the CSS's own `oklch()` values, converted at runtime.**
`frontend/src/styles/globals.css` defines every colour as `oklch(0.55 0.21 285)`. Pasting a
hand-converted `#6b53e4` here would leave two independent copies of the palette that drift
the first time either is touched, with no way to notice - a slightly wrong indigo is not
something anyone spots in review. So `theme/tokens.dart` holds the same numbers and
`theme/oklch.dart` does the conversion. `test/theme_test.dart` pins the results against an
independent implementation of the same OKLab matrices.

**Money never passes through a `double`.** The backend serialises `Decimal` as a decimal
string precisely so no float is involved; `1234567.89` as an IEEE-754 double is
`1234567.8899999999`. Dart's `NumberFormat.format` takes a `num`, so using it would
reintroduce exactly what the string exists to avoid. `core/format.dart` therefore does its
own digit grouping and scales to `BigInt` for arithmetic. `double` appears only for chart
geometry and confidence thresholds, and says so at every one of those call sites.

**The refresh token is held in a cookie jar this code cannot read.** The backend never
returns it in a response body - it arrives as `Set-Cookie` and goes back the same way - so
the client behaves exactly as the browser does. The access token lives in a field that dies
with the process; the jar is persisted, because a browser keeps its cookie across a tab
close and an app that forgot the session on every quit would be worse than the thing it is
copying, not safer.

---

## Staying signed in

Quit the app and open it again and you are still signed in. That is not a stored password:
the refresh cookie is kept in a per-user file jar, and on start-up the client exchanges it
for a fresh access token before the first frame - the same exchange the web app makes on a
page reload, and the same one the backend already supports for non-browser clients.

Three properties make it hold up rather than merely appear to work:

- **The rotated cookie is written back every time.** Each refresh mints a new token and
  revokes the old one, and the backend treats a replayed token as a breach - so a client
  that failed to save the successor would present a revoked credential on the *next* launch
  and have the whole session lineage revoked. Verified over three consecutive relaunches of
  the release binary, with zero reuse detections server-side.
- **"Keep me signed in" is on by default**, where the web app leaves it off. The backend
  reads it as a session lifetime: 7 days without it, `REFRESH_TOKEN_TTL_DAYS` (30 by
  default) with it. Off is right for a browser that might be shared or public; an installed
  desktop client in a per-user directory is neither, and being asked to sign in weekly to
  software on your own desk buys nothing. The checkbox is still there for a shared
  workstation.
- **Signing out really signs out.** The jar is emptied, so the next launch cannot get back
  in - and the same happens when a session expires or is revoked remotely, so a dead cookie
  is not re-presented on every start.

**Sign-out tears down locally *first*, then tells the server.** That ordering looks
backwards and is load-bearing, so it is worth stating before someone tidies it: the button
discards the future `signOut` returns, and Dio waits 30 seconds before giving up. Awaiting
the request first therefore meant that against a slow or unreachable API, pressing "Sign
out" produced no redirect, no spinner and no error - a dead button, and an easy thing to
read as "the app cannot reach the backend". Signing out is a local act; the request is a
courtesy that revokes the refresh token early.

The credentials outlive the UI teardown on purpose, though. A logout that arrived
unauthenticated would revoke nothing and leave the refresh token valid for its full 30 days,
so the access token and the jar are held until the request resolves or a three-second grace
period lapses, whichever comes first. `test/sign_out_test.dart` pins both halves against an
API whose logout never returns.

**Re-authentication still comes round eventually, and that is deliberate.** The backend
preserves a session's original expiry across rotation, so refreshing cannot extend a session
indefinitely. Nothing on this side can change that, and nothing on this side should try.

---

## Where the desktop honestly differs

Five places, and each is a platform limit or a deliberate boundary rather than a shortcut.

| The web app | Here | Why |
| --- | --- | --- |
| `<input type="date">` | Material's date picker | Flutter has no native date field. The value stays an ISO string either way, because that is what the API takes. |
| PDF preview in an `<iframe>` | Opens in the machine's own PDF viewer | A desktop app has no built-in PDF renderer, and bundling a rasteriser for a preview is a large dependency. The recognised text is still shown inline, and it is the half that answers "where did this figure come from" a year later. |
| Drag-and-drop upload | File picker | Same endpoint, same validation, same duplicate warning. |
| `window.confirm` / `window.prompt` | Real dialogs | The web app notes it uses those only because a dialog system belonged with the rest of its UI kit. That kit exists here, so these are proper dialogs - with the wording carried over verbatim, because each one names what will and will not happen. |
| Verifies a proof bundle in-page | **Does not, on purpose** | The web client carries a second, independent implementation of the canonical encoding so a verifier's browser can check a proof without trusting our API. A third implementation in Dart would be a third thing to keep byte-identical, and nobody verifies a supplier's books from the supplier's own desktop install. [`trust_api.dart`](lib/api/trust_api.dart) therefore has **no** `verifyBundle` - the Trust screen exports a bundle and points at `/verify`. |

Everything else - the 248px sidebar, the glass header, ⌘K, the tabs in the URL, the info
tips, the reversal-not-delete flow, the exact wording of every explanation - is the same.

---

## Tests

```bash
flutter test
```

105 unit and widget tests, plus one integration test that marks itself skipped when the
backend is not running - so `flutter test` stays usable without Docker.

The unit tests cover what must not be approximately right: the money path (exact decimal
formatting, `BigInt` summation, scale-insensitive comparison), the OKLCH conversion, the
date rules, the decimal input filter, and the card-number checks.

`test/interaction_test.dart` asserts that pressing a button calls its callback. That sounds
too trivial to test, and it is there because its absence let a bug ship in which **every
button in the app was dead**. `AppButton` wrapped its content in an `InkWell` carrying
`onPressed`, and *inside* that put a `GestureDetector` declaring `onTapDown`/`onTapUp` to
animate the press. Two tap recognizers, one gesture arena, and the deeper one wins - so the
`GestureDetector` claimed every tap and `InkWell.onTap` never fired.

Nothing logged, nothing threw, `flutter analyze` was clean, and the button still animated
under the cursor, so it read as "the callbacks are broken" rather than "the tap is stolen two
widgets down". **All tap handling now lives on the `InkWell` alone** - callback and press
animation both. Adding a gesture detector beneath it would reintroduce this exactly.

`test/accounts_widget_test.dart` pumps the accounts panel and the transfer form in both
themes and at a narrow window. That is a narrow question - *does it build* - and it has its
own file because `flutter analyze` cannot answer it: the one UI bug here that reached a
release binary was a `Builder` closure that captured the variable it was being assigned to,
so it returned a widget containing itself. Clean analyze, clean unit suite, stack overflow
on the first frame. A widget overflowing its constraints raises in a test too, so the
narrow-window cases catch a layout that only breaks when the window is dragged small.

`test/accounts_test.dart` is mostly about one trap. **A debit card arrives from the API with
the same `id` as the bank account it draws on**, because it is not a separate place money
lives - so the picker keys on `card_id ?? id`, and a transfer form deduplicates by `id`.
Both would look correct in a screenshot and be wrong in use: two `<option>`-equivalents
sharing a value means selecting the card silently snaps back to the bank account. The tests
pin the key, the resolution back to an account id, and the ordering the deduplication relies
on.

The date cases run under the **default** configuration on purpose - rupees, so locale
`en_IN`. `intl` bundles date symbols for `en_US` only, so a suite that formatted dates only
under USD would pass while every date in the shipped app threw. That is not hypothetical: it
is what happened, and it surfaced only once a session existed, because the sign-in screen
renders no dates.

`test/session_integration_test.dart` drives the real thing - register, verify using the
token out of the actual email, sign in, then **discard the access token and
restore the session from the cookie alone**, and then do it again through a freshly
constructed client that has to read the cookie back off disk, which is what a relaunched
process does. That last step is what a user experiences as
"it remembered me", and it has no unit-testable surface: whether a Dart cookie jar honours
the backend's `path=/api/v1/auth` scope, and whether the backend accepts what it sends
back, is a question only the real server can answer. It marks itself skipped when the stack
is not running, so `flutter test` stays usable without Docker.
