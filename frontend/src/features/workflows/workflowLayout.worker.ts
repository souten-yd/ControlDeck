/// <reference lib="webworker" />
import { computeLayeredLayout, type LayoutEdgeInput, type LayoutNodeInput } from "./largeFlow";

self.onmessage = (event: MessageEvent<{ requestId: number; nodes: LayoutNodeInput[]; edges: LayoutEdgeInput[] }>) => {
  const { requestId, nodes, edges } = event.data;
  self.postMessage({ requestId, positions: computeLayeredLayout(nodes, edges) });
};

export {};
