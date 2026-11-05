import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AdminApprovalPage } from "./features/admin/AdminApprovalPage";
import { LoginPage } from "./features/auth/LoginPage";
import { VerifyPage } from "./features/auth/VerifyPage";
import { ChatPage } from "./features/chat/ChatPage";
import { CourseCreatePage } from "./features/courses/CourseCreatePage";
import { CourseDetailPage } from "./features/courses/CourseDetailPage";
import { CourseListPage } from "./features/courses/CourseListPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { SearchPage } from "./features/search/SearchPage";
import { StatusPage } from "./features/status/StatusPage";
import { ViewerPage } from "./features/viewer/ViewerPage";

// "/" renders the authenticated dashboard (redirects to /login on a 401
// from /auth/me); "/admin" is the professor-role-request approval queue
// - it redirects non-admins to "/" itself on a 403 from
// GET /auth/role-requests; "/status" keeps the health
// check reachable for deployment verification. "/courses" (all authenticated
// users) and "/courses/new" (professor/admin) are the
// browse+join and create routes; "/courses/:id" is the upload
// + status + delete + retry course detail route.
// "/courses/:id/search" is the per-course hybrid search page.
// "/courses/:id/books/:bookId" is the built-in PDF viewer
// - deep-links to `?page=N`.
// "/courses/:id/chat" is the course-scoped RAG chat page
// - a POST-SSE streamed conversation grounded in the
// course's materials.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/auth/verify" element={<VerifyPage />} />
        <Route path="/admin" element={<AdminApprovalPage />} />
        <Route path="/status" element={<StatusPage />} />
        <Route path="/courses" element={<CourseListPage />} />
        <Route path="/courses/new" element={<CourseCreatePage />} />
        <Route path="/courses/:id" element={<CourseDetailPage />} />
        <Route path="/courses/:id/search" element={<SearchPage />} />
        <Route path="/courses/:id/chat" element={<ChatPage />} />
        <Route path="/courses/:id/books/:bookId" element={<ViewerPage />} />
      </Routes>
    </BrowserRouter>
  );
}
