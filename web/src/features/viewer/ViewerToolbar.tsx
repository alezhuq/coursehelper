import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { MAX_ZOOM, MIN_ZOOM } from "./ViewerPage";

interface ViewerToolbarProps {
  courseId: string;
  page: number;
  numPages: number | null;
  zoom: number;
  onPrev: () => void;
  onNext: () => void;
  onJump: (page: number) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
}

/**
 * Sticky viewer toolbar: back
 * link, prev/next page, a numeric jump-to-page input, and zoom out/in.
 * Deliberately no in-PDF text search control anywhere (redundant
 * with the course hybrid search page). Every icon-only button carries an
 * `aria-label`; all controls disable while `numPages` is unknown.
 *
 * The page-jump input keeps its own local `draft` string, synced from
 * `page` whenever it changes externally (prev/next/a fresh citation) - it
 * only commits (and clamps/reverts) on Enter or blur, per the E5
 * "out-of-range/invalid entry" and "empty/NaN reverts to current page"
 * form-state contract.
 */
export function ViewerToolbar({
  courseId,
  page,
  numPages,
  zoom,
  onPrev,
  onNext,
  onJump,
  onZoomIn,
  onZoomOut,
}: ViewerToolbarProps) {
  const navigate = useNavigate();
  const controlsDisabled = numPages === null;
  const [draft, setDraft] = useState(String(page));

  useEffect(() => {
    setDraft(String(page));
  }, [page]);

  function commitDraft() {
    const parsed = Number.parseInt(draft, 10);
    if (draft.trim() === "" || !Number.isFinite(parsed)) {
      setDraft(String(page));
      return;
    }
    onJump(parsed);
  }

  return (
    <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-slate-100 px-4 py-2">
      <Button
        type="button"
        variant="ghost"
        onClick={() => navigate(`/courses/${courseId}`)}
      >
        <ChevronLeft />
        Back to course
      </Button>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Previous page"
          disabled={controlsDisabled || page <= 1}
          onClick={onPrev}
        >
          <ChevronLeft />
        </Button>

        <Input
          type="number"
          className="w-16"
          min={1}
          max={numPages ?? undefined}
          value={draft}
          disabled={controlsDisabled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") commitDraft();
          }}
          onBlur={commitDraft}
        />
        <Label className="whitespace-nowrap">of {numPages ?? "…"}</Label>

        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Next page"
          disabled={controlsDisabled || (numPages !== null && page >= numPages)}
          onClick={onNext}
        >
          <ChevronRight />
        </Button>

        <div className="h-6 w-px bg-slate-300" />

        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Zoom out"
          disabled={controlsDisabled || zoom <= MIN_ZOOM}
          onClick={onZoomOut}
        >
          <ZoomOut />
        </Button>
        <Label className="whitespace-nowrap">{Math.round(zoom * 100)}%</Label>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Zoom in"
          disabled={controlsDisabled || zoom >= MAX_ZOOM}
          onClick={onZoomIn}
        >
          <ZoomIn />
        </Button>
      </div>
    </div>
  );
}
