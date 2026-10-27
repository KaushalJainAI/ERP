import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (failureCount, error: any) => {
        const status = error?.response?.status;
        // Client errors (400/401/403/404) will not heal by retrying.
        if (status && status >= 400 && status < 500) return false;
        return failureCount < 1;
      },
    },
  },
});
