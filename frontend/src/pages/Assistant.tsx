import { lazy, Suspense } from "react";
import { useNavigate } from "react-router-dom";

const AssistantChat = lazy(() => import("../features/workflows/AssistantChat"));

/** ワークフロー画面に従属しないAIアシスタントの第一級route。 */
export default function AssistantPage() {
  const navigate = useNavigate();
  return <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">AIアシスタントを読み込み中...</div>}>
    <AssistantChat onClose={() => navigate("/")} />
  </Suspense>;
}
