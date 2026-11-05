import { useNavigate } from "react-router-dom";

import { Card } from "@/components/ui/card";

import { renderSnippet } from "./highlightSnippet";

export interface SearchResult {
  book_id: string;
  original_filename: string;
  section_path: string[];
  page_start: number;
  page_end: number;
  snippet: string;
}

interface SearchResultCardProps {
  courseId: string;
  result: SearchResult;
}

/**
 * One search result row: fully clickable, navigates to the viewer route
 * at the result's starting page (the book_id + ?page=N citation contract). Mirrors CourseListPage's
 * <Card className="cursor-pointer ... hover:bg-slate-100"> whole-card-
 * clickable pattern.
 */
export function SearchResultCard({ courseId, result }: SearchResultCardProps) {
  const navigate = useNavigate();
  const pageLabel =
    result.page_start === result.page_end
      ? `p. ${result.page_start}`
      : `p. ${result.page_start}–${result.page_end}`;

  return (
    <Card
      className="cursor-pointer gap-0 px-6 py-4 transition-colors hover:bg-slate-100"
      onClick={() =>
        navigate(`/courses/${courseId}/books/${result.book_id}?page=${result.page_start}`)
      }
    >
      <p className="break-words text-xl font-semibold leading-[1.2] text-slate-900">
        {result.original_filename}
      </p>
      {result.section_path.length > 0 && (
        <p className="mt-1 break-words text-sm leading-[1.5] text-slate-500">
          {result.section_path.join(" › ")}
        </p>
      )}
      <p className="mt-1 text-sm leading-[1.5] text-slate-500">{pageLabel}</p>
      <p className="mt-2 break-words text-base leading-[1.5] text-slate-900">
        {renderSnippet(result.snippet)}
      </p>
    </Card>
  );
}
