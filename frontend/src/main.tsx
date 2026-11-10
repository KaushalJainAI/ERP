import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Offline shell and install support. Production builds only: the dev server is not cached.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  try {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* registration refused (e.g. private window): the app works online as before */
    });
  } catch {
    /* serviceWorker unavailable */
  }
}
