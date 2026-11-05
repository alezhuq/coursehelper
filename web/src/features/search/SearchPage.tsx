import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { AppHeader } from "../shared/AppHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { SearchResultCard, type SearchResult } from "./SearchResultCard";

interface Course {
  id: string;
  owner_id: string;
  code: string;
  title: string;
  term: string;
  created_at: string;
}

type PageState =
  | { kind: "loading" }
  | { kind: "ok"; course: Course }
  | { kind: "error"; message: string };

function resultCountLabel(count: number): string {
  if (count === 20) return "Showing the top 20 matches";
  return count === 1 ? "1 result" : `${count} results`;
}

/**
 * `/courses/:id/search`.
 * Mirrors CourseDetailPage's loading|ok|error page state machine and
 * 401->/login / 403->/ redirect convention for the course header fetch.
 *
 * Explicit-submit search: Enter or the Search button POSTs
 * /courses/:id/search; the button disables and relabels "Searching..."
 * while in flight (identical convention to the existing "Creating.../
 * Deleting..." button-label pattern), and stays disabled while the
 * trimmed query is empty so a blank query never runs a pointless request.
 *
 * The results region is a second, nested state machine, independent of
 * the course-header loading|ok|error above: idle (no search submitted
 * yet this page load) | loading | populated (1-20 results, with a
 * result-count line disambiguating the no-pagination top-20 cap)
 * | no-results (echoes the submitted query) | error (replaces
 * the region entirely, preserves the typed query, offers a Try again
 * retry of the last submitted query). Loading always clears any previous
 * result set first, so stale results never linger under a new query.
 */
export function SearchPage() {
  const { id: courseId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [lastQuery, setLastQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);

  const loadCourse = useCallback(async () => {
    if (!courseId) return;
    setState({ kind: "loading" });
    try {
      const coursesResponse = await apiFetch("/courses");
      const courses = (await coursesResponse.json()) as Course[];
      const course = courses.find((candidate) => candidate.id === courseId);
      if (!course) {
        setState({ kind: "error", message: "Could not load this page." });
        return;
      }
      setState({ kind: "ok", course });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      if (error instanceof ApiError && error.status === 403) {
        navigate("/", { replace: true });
        return;
      }
      setState({ kind: "error", message: "Could not load this page." });
    }
  }, [courseId, navigate]);

  useEffect(() => {
    void loadCourse();
  }, [loadCourse]);

  const trimmedQuery = query.trim();

  const runSearch = useCallback(
    async (q: string) => {
      if (!courseId) return;
      setSearching(true);
      setSearchError(null);
      setResults([]);
      try {
        const response = await apiFetch(`/courses/${courseId}/search`, {
          method: "POST",
          body: JSON.stringify({ q }),
        });
        const nextResults = (await response.json()) as SearchResult[];
        setResults(nextResults);
        setLastQuery(q);
        setHasSearched(true);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          navigate("/login", { replace: true });
          return;
        }
        if (error instanceof ApiError && error.status === 403) {
          navigate("/", { replace: true });
          return;
        }
        setLastQuery(q);
        setHasSearched(true);
        setSearchError("Could not load search results. Try again.");
      } finally {
        setSearching(false);
      }
    },
    [courseId, navigate],
  );

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (searching || !trimmedQuery) return;
    void runSearch(trimmedQuery);
  }

  function handleRetry() {
    if (!lastQuery) return;
    void runSearch(lastQuery);
  }

  if (state.kind === "loading") {
    return (
      <main>
        <AppHeader />
        <p className="py-16 text-center text-sm text-slate-500">Loading...</p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main>
        <AppHeader />
        <div className="mx-auto max-w-[720px] px-8 py-8">
          <Alert variant="destructive">
            <AlertDescription>{state.message}</AlertDescription>
          </Alert>
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => void loadCourse()}
          >
            Try again
          </Button>
        </div>
      </main>
    );
  }

  const { course } = state;

  return (
    <main>
      <AppHeader />
      <div className="mx-auto max-w-[720px] px-8 py-8">
        <h1 className="text-[28px] font-semibold leading-[1.2] text-slate-900">
          {course.code} {course.title}
        </h1>
        <p className="mt-1 text-sm leading-[1.5] text-slate-500">
          Search this course's materials
        </p>

        <form className="mt-6 flex gap-2" onSubmit={handleSubmit}>
          <Input
            type="search"
            placeholder="Search this course's materials"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button type="submit" disabled={searching || !trimmedQuery}>
            {searching ? "Searching..." : "Search"}
          </Button>
        </form>

        <div className="mt-6">
          {searchError ? (
            <>
              <Alert variant="destructive">
                <AlertDescription>{searchError}</AlertDescription>
              </Alert>
              <Button
                type="button"
                variant="outline"
                className="mt-4"
                onClick={handleRetry}
              >
                Try again
              </Button>
            </>
          ) : searching ? (
            <p className="py-16 text-center text-sm text-slate-500">
              Searching…
            </p>
          ) : !hasSearched ? (
            <p className="py-16 text-center text-sm text-slate-500">
              Type a question or keyword above and press Search.
            </p>
          ) : results.length === 0 ? (
            <div className="py-16 text-center">
              <p className="text-xl font-semibold leading-[1.2] text-slate-900">
                No matches in this course's materials
              </p>
              <p className="mt-2 text-base leading-[1.5] text-slate-500">
                No results for “{lastQuery}”.
              </p>
            </div>
          ) : (
            <>
              <p className="text-sm leading-[1.5] text-slate-500">
                {resultCountLabel(results.length)}
              </p>
              <ul className="mt-2 flex flex-col gap-4">
                {results.map((result, index) => (
                  <li key={`${result.book_id}-${index}`}>
                    <SearchResultCard courseId={course.id} result={result} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
