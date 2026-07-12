/** Ubuntu Control Deck ロゴ。
 * モチーフ: コントロールデッキ（ミキサー）のスライダー 3 本。
 * 背景はアクセントカラーに追従する。 */
export function Logo({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 48 48"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Ubuntu Control Deck"
    >
      <rect x="2" y="2" width="44" height="44" rx="12" fill="var(--color-accent-600)" />
      {/* スライダーレール */}
      <g stroke="rgba(255,255,255,0.45)" strokeWidth="3" strokeLinecap="round">
        <path d="M14 12v24" />
        <path d="M24 12v24" />
        <path d="M34 12v24" />
      </g>
      {/* ノブ（状態はそれぞれ異なる位置） */}
      <g fill="#ffffff">
        <rect x="10" y="24" width="8" height="7" rx="2.5" />
        <rect x="20" y="14" width="8" height="7" rx="2.5" />
        <rect x="30" y="29" width="8" height="7" rx="2.5" />
      </g>
    </svg>
  );
}
