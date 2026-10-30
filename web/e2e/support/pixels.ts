import type { Locator, Page } from "@playwright/test";

/**
 * Fraction of pixels in ``locator``'s screenshot that differ from
 * ``key`` (an RGB chroma colour) by more than ``tolerance`` per channel.
 * Used to assert that a VRM character actually rendered on the green
 * screen — a black/empty/failed canvas reads as ~0.
 */
export async function nonKeyPixelRatio(
  page: Page,
  locator: Locator,
  key: [number, number, number],
  tolerance = 40,
): Promise<number> {
  const png = await locator.screenshot({ animations: "disabled" });
  const b64 = png.toString("base64");
  return page.evaluate(
    async ({ b64, key, tolerance }) => {
      const img = new Image();
      img.src = `data:image/png;base64,${b64}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width;
      c.height = img.height;
      const ctx = c.getContext("2d");
      if (!ctx) return 0;
      ctx.drawImage(img, 0, 0);
      const { data } = ctx.getImageData(0, 0, c.width, c.height);
      let off = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (
          Math.abs(data[i] - key[0]) > tolerance ||
          Math.abs(data[i + 1] - key[1]) > tolerance ||
          Math.abs(data[i + 2] - key[2]) > tolerance
        ) {
          off++;
        }
      }
      return off / (data.length / 4);
    },
    { b64, key, tolerance },
  );
}
