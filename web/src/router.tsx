import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { SignIn } from "./pages/SignIn";
import { Home } from "./pages/Home";
import { Runs } from "./pages/Runs";
import { RunDetail } from "./pages/RunDetail";
import { Surface } from "./pages/Surface";
import { Findings } from "./pages/Findings";
import { Behaviours } from "./pages/Behaviours";
import { Agents } from "./pages/Agents";
import { Policy } from "./pages/Policy";
import { Ledger } from "./pages/Ledger";
import { Settings } from "./pages/Settings";
import { routes } from "./lib/routes";

export const router = createBrowserRouter([
  { path: routes.signin, element: <SignIn /> },
  {
    element: <AppShell />,
    children: [
      { path: routes.home, element: <Home /> },
      { path: routes.runs, element: <Runs /> },
      { path: routes.runPattern, element: <RunDetail /> },
      { path: routes.surface, element: <Surface /> },
      { path: routes.findings, element: <Findings /> },
      { path: routes.behaviours, element: <Behaviours /> },
      { path: routes.agents, element: <Agents /> },
      { path: routes.policy, element: <Policy /> },
      { path: routes.ledger, element: <Ledger /> },
      { path: routes.settings, element: <Settings /> },
    ],
  },
]);
