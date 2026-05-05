import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { reportDiagnostic } from "./diagnostics";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 },
  },
});

// Capture uncaught render-time + async errors at the window level so that
// even outside React's tree we get a server-side audit trail.
if (typeof window !== "undefined") {
  window.addEventListener("error", (e) => {
    reportDiagnostic(
      "window_error",
      e.message || "window error",
      { context: { stack: e.error?.stack, source: e.filename } },
    );
  });
  window.addEventListener("unhandledrejection", (e) => {
    const reason: any = e.reason;
    reportDiagnostic(
      "unhandled_rejection",
      typeof reason === "string" ? reason : reason?.message || "unhandled rejection",
      { context: { stack: reason?.stack } },
    );
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </BrowserRouter>
    </ErrorBoundary>
  </React.StrictMode>
);
