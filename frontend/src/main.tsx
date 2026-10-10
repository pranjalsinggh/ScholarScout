import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";
import { installExitCleanup } from "./services/api";
import { installAppRecovery } from "./recovery";
import { RootErrorBoundary } from "./components/glass/RootErrorBoundary";

installExitCleanup();
installAppRecovery();

// Suppress the browser context menu across all routes and recovery screens.
// This is a UI preference; frontend code remains accessible to the browser.
document.addEventListener("contextmenu", (event) => event.preventDefault(), { capture: true });

try {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <RootErrorBoundary>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </RootErrorBoundary>
    </React.StrictMode>,
  );
} catch (e) {
  // synchronous mount failure lands in the watchdog's recovery card
  console.error("ScholarScout failed to mount:", e);
}
