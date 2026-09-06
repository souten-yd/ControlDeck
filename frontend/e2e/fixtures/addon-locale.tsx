import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EmbeddedAddonView } from "../../src/features/addons/EmbeddedAddonView";
import "../../src/styles/index.css";

// Browser component fixture only: API and opaque child are intercepted by the test.
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={new QueryClient()}>
    <BrowserRouter>
      <EmbeddedAddonView
        addon={{ id: "locale-fixture", name: "Locale fixture", state: "healthy", health: null }}
        contribution={{ addon_id: "locale-fixture", id: "workspace", label: "Workspace",
          permission: "", availability: "available", path: "/", mobile: "embedded" }}
        routePath="/"
      />
    </BrowserRouter>
  </QueryClientProvider>,
);
