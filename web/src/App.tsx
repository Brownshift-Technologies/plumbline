import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { IconSprite } from "./components/Icon";
import { ToastProvider } from "./components/Toast";

export function App() {
  return (
    <ToastProvider>
      <IconSprite />
      <RouterProvider router={router} />
    </ToastProvider>
  );
}
