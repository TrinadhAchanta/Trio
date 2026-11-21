"use client";

import { Download, ImageIcon, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface ImageModel {
  id: string;
  label: string;
  provider: string;
  sizes: string[];
  price_per_image_usd: number | null;
  source: string;
  configured: boolean;
  mock: boolean;
}
interface Gen {
  id: string;
  prompt: string;
  provider: string;
  model: string;
  size: string;
  mock: boolean;
  url: string | null;
  revised_prompt: string | null;
  cost_usd: number;
  created_at: string;
}

function priceLabel(m: ImageModel) {
  if (!m.configured) return " (no API key)";
  if (m.price_per_image_usd) return ` (about ${m.price_per_image_usd.toFixed(3)} USD)`;
  return "";
}

export function ImagesPanel() {
  const [models, setModels] = useState<ImageModel[]>([]);
  const [images, setImages] = useState<Gen[]>([]);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("auto");
  const [size, setSize] = useState("1024x1024");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api<Gen[]>("/api/images").then(setImages), []);
  useEffect(() => {
    Promise.all([api<{ models: ImageModel[] }>("/api/images/models").then((r) => setModels(r.models)), load()]).catch((e) => setError(e.message));
  }, [load]);

  const usable = models.filter((m) => m.configured);
  const selected = model === "auto" ? (usable.find((m) => !m.mock) ?? usable[0]) : models.find((m) => m.id === model);
  const sizes = selected?.sizes ?? ["1024x1024"];
  const effectiveSize = sizes.includes(size) ? size : sizes[0];

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/images", { method: "POST", json: { prompt, model, size: effectiveSize } });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Image generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function deleteImage(id: string) {
    if (!confirm("Delete this image?")) return;
    await api(`/api/images/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
        <div>
          <h1 className="text-xl font-semibold">Images</h1>
          <p className="mt-1 text-sm text-muted">Describe an image and Trio generates it with the selected image model. Images are saved to your files.</p>
        </div>
        {selected?.mock && (
          <p className="rounded-lg border border-accent-3/40 bg-accent-3/10 p-3 text-sm" data-testid="images-demo-note">
            Demo mode: no image model key is set, so Trio draws a labeled placeholder instead of calling an AI model.
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {error}
          </p>
        )}
        <form
          className="rounded-2xl border border-line bg-panel p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (prompt.trim().length >= 3) void generate();
          }}
        >
          <label htmlFor="image-prompt" className="text-sm font-medium">
            Describe the image
          </label>
          <textarea
            id="image-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={2}
            placeholder="e.g. A watercolor lighthouse at dusk, soft light"
            className="mt-1 w-full resize-none rounded-lg border border-line bg-bg px-2 py-1.5 text-sm"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-1.5 text-xs">
              Image model
              <select value={model} onChange={(e) => setModel(e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1 text-sm">
                <option value="auto">Auto</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id} disabled={!m.configured}>
                    {m.label}
                    {priceLabel(m)}
                  </option>
                ))}
              </select>
            </label>
            <label className="inline-flex items-center gap-1.5 text-xs">
              Size
              <select value={effectiveSize} onChange={(e) => setSize(e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1 text-sm">
                {sizes.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <button
              disabled={busy || prompt.trim().length < 3 || usable.length === 0}
              className="ml-auto rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-contrast disabled:opacity-40"
            >
              {busy ? "Generating..." : "Generate"}
            </button>
          </div>
        </form>

        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="image-gallery">
          {images.length === 0 && (
            <li className="col-span-full flex items-center gap-2 text-sm text-muted">
              <ImageIcon size={16} /> No images yet.
            </li>
          )}
          {images.map((g) => (
            <li key={g.id} className="overflow-hidden rounded-2xl border border-line bg-panel">
              {g.url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={g.url} alt={g.prompt} className="aspect-square w-full bg-panel-2 object-cover" loading="lazy" />
              )}
              <div className="space-y-1 p-2 text-xs">
                <p className="line-clamp-2">{g.prompt}</p>
                <div className="flex items-center gap-1 text-muted">
                  <span className="truncate">
                    {g.mock ? "Demo placeholder" : `${g.provider}:${g.model}`} · {g.size}
                  </span>
                  {g.url && (
                    <a href={g.url} download className="ml-auto rounded p-1 hover:bg-panel-2" aria-label="Download image">
                      <Download size={14} />
                    </a>
                  )}
                  <button onClick={() => void deleteImage(g.id)} className="rounded p-1 hover:bg-panel-2 hover:text-danger" aria-label="Delete image">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
