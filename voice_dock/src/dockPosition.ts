export type DockPosition = { x: number; y: number };
export type RectangleSize = { width: number; height: number };

export function clampDockPosition(
  position: DockPosition,
  dock: RectangleSize,
  viewport: RectangleSize,
  margin = 8
): DockPosition {
  const maxX = Math.max(margin, viewport.width - dock.width - margin);
  const maxY = Math.max(margin, viewport.height - dock.height - margin);
  return {
    x: Math.min(Math.max(position.x, margin), maxX),
    y: Math.min(Math.max(position.y, margin), maxY)
  };
}

export function parseStoredDockPosition(value: string | null): DockPosition | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<DockPosition>;
    if (!Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) return null;
    return { x: Number(parsed.x), y: Number(parsed.y) };
  } catch {
    return null;
  }
}
