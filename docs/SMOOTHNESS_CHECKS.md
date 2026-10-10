# Interface smoothness

Updated 30 September 2026.

## Changes

- Replaced the continuously animated decorative network with a static texture that redraws on size changes. Ambient light uses feathered gradients without large blur filters or perpetual drift.
- Kept the liquid glass appearance while limiting backdrop blur to outer panes. The landing page's filtered surfaces decreased from **22 to 13** in the same desktop viewport. Chips, inputs and nested soft panes no longer filter the background independently.
- Removed the full-page route fade and exit delay. Content entrances remain short; unchanged dashboard polling responses no longer trigger a new render.
- The research graph stops drawing once its force layout settles and pauses when hidden or outside the viewport. Pan, zoom, selection and filters request a redraw. Gesture handling rebases the remaining finger after a pinch and distinguishes a slow drag from a tap.
- Chat batches streamed text updates, memoizes completed message bubbles, and scrolls only its conversation pane. Reading earlier answers stops automatic following. Leaving the view aborts both stream and classic fallback requests in the browser.
- Added native smooth navigation to the landing page's **How it works** and **Features** sections, a hero scroll link, and one-time section reveals. Mouse wheel and touch scrolling remain native. Reduced motion disables smooth scrolling and landing/nav motion.

## Verification

Checks use the built frontend and a separate local backend data directory with cloud synthesis disabled. No private PDFs are needed for the interface checks.

- `npm run build`: TypeScript and the final Vite production build passed.
- `npm run verify:render`: synthetic component runtime checks passed at 60 and 120 Hz. The 80-node graph stopped within 300 simulation draws, made no continuing idle requests, and redrew once for zoom. Hidden/offscreen pause, cleanup, reduced motion, slow dragging and pinch-to-pan continuity passed.
- The same harness delivered 100 token events plus evidence and completion. Chat made six message state updates, retained the exact final text and evidence, cleared rejected text on fallback, and aborted a classic fallback on leaving without stale writes. These are synthetic checks, not a live cloud-provider benchmark.
- Production browser checks passed for desktop and 390-pixel phone layouts: landing anchors, menu dismissal, Home returning to the top, graph filtering/zoom and a completed local chat answer. The mobile hero link ended with its section 112 pixels below the viewport top, clear of the fixed navbar. No horizontal overflow was found in landing or chat, and no console warnings/errors were captured in the demo run.
- The temporary demo session, uploaded copy, passages, chats and caches were erased and checked empty. Temporary preview servers were stopped. Original PDFs and the user's environment settings were unchanged.

The filtered-surface count is a browser observation, not a frame-rate benchmark. Device-specific frame rates still depend on browser, GPU, power mode and document size.
