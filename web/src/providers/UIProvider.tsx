import { ReactNode } from "react";
import { ConfirmProvider } from "../components/ui/ConfirmDialog";
import { Toaster } from "../components/ui/Toaster";

export function UIProvider({ children }: { children: ReactNode }) {
  return (
    <ConfirmProvider>
      {children}
      <Toaster />
    </ConfirmProvider>
  );
}

export default UIProvider;
