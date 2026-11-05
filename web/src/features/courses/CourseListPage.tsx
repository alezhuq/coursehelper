import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiFetch, ApiError } from "../../api/client";
import { AppHeader } from "../shared/AppHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  auth_method: string;
}

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
  | { kind: "ok"; user: CurrentUser; courses: Course[] }
  | { kind: "error"; message: string };

/**
 * `/courses`: browse + self-join, all authenticated
 * users. Mirrors AdminApprovalPage's loading|ok|error state machine and
 * DashboardPage's parallel-fetch pattern (GET /auth/me + GET /courses).
 *
 * GET /courses does not return per-user enrollment,
 * so "already joined" is tracked purely in local state after a successful
 * join call - joining is idempotent server-side, so re-clicking an
 * already-joined course is harmless even across a page reload.
 */
export function CourseListPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [joinedIds, setJoinedIds] = useState<Set<string>>(new Set());
  const [joiningId, setJoiningId] = useState<string | null>(null);
  const navigate = useNavigate();

  const loadCourses = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const [userResponse, coursesResponse] = await Promise.all([
        apiFetch("/auth/me"),
        apiFetch("/courses"),
      ]);
      const user = (await userResponse.json()) as CurrentUser;
      const courses = (await coursesResponse.json()) as Course[];
      setState({ kind: "ok", user, courses });
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
  }, [navigate]);

  useEffect(() => {
    void loadCourses();
  }, [loadCourses]);

  async function handleJoin(courseId: string) {
    setJoiningId(courseId);
    try {
      await apiFetch(`/courses/${courseId}/join`, { method: "POST" });
      setJoinedIds((previous) => new Set(previous).add(courseId));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
        return;
      }
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "Action failed",
      });
    } finally {
      setJoiningId(null);
    }
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
            onClick={() => void loadCourses()}
          >
            Try again
          </Button>
        </div>
      </main>
    );
  }

  const { user, courses } = state;
  const canCreateCourse = user.role === "professor" || user.role === "admin";

  return (
    <main>
      <AppHeader />
      <div className="mx-auto max-w-[720px] px-8 py-8">
        <div className="mb-8 flex items-center justify-between">
          <h1 className="text-[28px] font-semibold leading-[1.2] text-slate-900">
            Courses
          </h1>
          {canCreateCourse && (
            <Button type="button" onClick={() => navigate("/courses/new")}>
              Create course
            </Button>
          )}
        </div>

        {courses.length === 0 && (
          <div className="py-16 text-center">
            <p className="text-xl font-semibold leading-[1.2] text-slate-900">
              No courses yet
            </p>
            <p className="mt-2 text-base text-slate-500">
              {canCreateCourse
                ? "Create your first course to start building its library."
                : "When a professor creates a course, it will appear here."}
            </p>
          </div>
        )}

        <ul className="flex flex-col gap-4">
          {courses.map((course) => {
            const isJoined = joinedIds.has(course.id);
            const isJoining = joiningId === course.id;
            return (
              <li key={course.id}>
                <Card
                  className="cursor-pointer flex-row items-center justify-between px-6 py-4"
                  onClick={() => navigate(`/courses/${course.id}`)}
                >
                  <div>
                    <p className="text-xl font-semibold leading-[1.2] text-slate-900">
                      {course.code} - {course.title}
                    </p>
                    <p className="mt-1 text-sm leading-[1.5] text-slate-500">
                      {course.term}
                    </p>
                  </div>
                  {isJoined ? (
                    <Button type="button" variant="outline" disabled>
                      Joined
                    </Button>
                  ) : (
                    <Button
                      type="button"
                      disabled={isJoining}
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleJoin(course.id);
                      }}
                    >
                      Join course
                    </Button>
                  )}
                </Card>
              </li>
            );
          })}
        </ul>
      </div>
    </main>
  );
}
