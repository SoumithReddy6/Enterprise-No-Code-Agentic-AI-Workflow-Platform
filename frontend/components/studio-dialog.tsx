'use client';
import { useEffect, useRef, type ReactNode } from 'react';

/** Native modal behavior provides focus containment and Escape handling. */
export default function StudioDialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-label={title}
      onCancel={onClose}
    >
      {children}
    </dialog>
  );
}
