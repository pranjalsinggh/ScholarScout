import { useEffect, useRef } from "react";

/**
 * A quiet research-network texture. Drawing it once keeps the liquid glass
 * backdrop stable while scrolling instead of repainting a full viewport every
 * animation frame. Resize work is coalesced and the backing canvas stays at DPR 1.
 */
export function NetworkBackground({ density = 42 }: { density?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;
    let resizeFrame = 0;
    let paintedWidth = 0;
    let paintedHeight = 0;

    const makeSprite = (hue: number) => {
      const sprite = document.createElement("canvas");
      sprite.width = sprite.height = 32;
      const spriteContext = sprite.getContext("2d");
      if (!spriteContext) return sprite;
      const gradient = spriteContext.createRadialGradient(16, 16, 0, 16, 16, 16);
      gradient.addColorStop(0, `hsla(${hue}, 80%, 72%, 0.9)`);
      gradient.addColorStop(0.35, `hsla(${hue}, 80%, 62%, 0.5)`);
      gradient.addColorStop(1, `hsla(${hue}, 80%, 60%, 0)`);
      spriteContext.fillStyle = gradient;
      spriteContext.fillRect(0, 0, 32, 32);
      return sprite;
    };
    const sprites = [makeSprite(158), makeSprite(187)];

    const draw = () => {
      const width = Math.round(canvas.clientWidth);
      const height = Math.round(canvas.clientHeight);
      if (!width || !height || (paintedWidth === width && paintedHeight === height)) return;
      paintedWidth = width;
      paintedHeight = height;
      canvas.width = width;
      canvas.height = height;

      const mobile = width < 768;
      const count = Math.min(
        mobile ? 22 : 72,
        Math.max(mobile ? 10 : 14, Math.round((density * width * height) / (1600 * 900))),
      );
      // Stable seeded positions avoid a new pattern on address-bar/viewport resize.
      let seed = 73471;
      const random = () => {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        return seed / 4294967296;
      };
      const nodes = Array.from({ length: count }, () => ({
        x: random() * width,
        y: random() * height,
        radius: 2 + random() * 2.5,
        hue: random() < 0.5 ? 0 : 1,
      }));
      const linkDistance = 120;
      ctx.lineWidth = 1;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distanceSquared = dx * dx + dy * dy;
          if (distanceSquared >= linkDistance * linkDistance) continue;
          const intensity = 1 - Math.sqrt(distanceSquared) / linkDistance;
          ctx.strokeStyle = `hsla(170, 70%, 65%, ${intensity * 0.12})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
      for (const node of nodes) {
        const size = node.radius * 6;
        ctx.drawImage(sprites[node.hue], node.x - size / 2, node.y - size / 2, size, size);
      }
    };

    const queueResize = () => {
      if (resizeFrame) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        draw();
      });
    };
    draw();
    const observer = new ResizeObserver(queueResize);
    observer.observe(canvas);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(resizeFrame);
    };
  }, [density]);

  return (
    <canvas
      ref={ref}
      className="pointer-events-none fixed inset-0 -z-10 h-full w-full opacity-50"
      aria-hidden
    />
  );
}

/** Static ambient light lets frosted surfaces stay inexpensive during scroll. */
export function AuroraField() {
  return (
    <div className="aurora-field" aria-hidden>
      <div className="aurora-blob violet" style={{ width: 440, height: 440, top: "-10%", left: "60%" }} />
      <div className="aurora-blob azure" style={{ width: 400, height: 400, top: "32%", left: "-12%" }} />
      <div className="aurora-blob sky" style={{ width: 320, height: 320, bottom: "-14%", left: "38%" }} />
    </div>
  );
}
