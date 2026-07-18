import { useEffect, useState } from 'react';
import { Platform } from 'react-native';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let createPortalFn: ((children: React.ReactNode, container: any) => React.ReactPortal) | null = null;

if (Platform.OS === 'web') {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const ReactDOM = require('react-dom');
    createPortalFn = ReactDOM.createPortal;
  } catch {
    createPortalFn = null;
  }
}

export function Portal({ children }: { children: React.ReactNode }) {
  const [container] = useState(() => {
    if (Platform.OS === 'web' && typeof document !== 'undefined') {
      return document.createElement('div');
    }
    return null;
  });

  useEffect(() => {
    if (container) {
      document.body.appendChild(container);
      return () => {
        document.body.removeChild(container);
      };
    }
  }, [container]);

  if (!createPortalFn || !container) {
    return children as React.ReactElement;
  }

  return createPortalFn(children, container);
}
