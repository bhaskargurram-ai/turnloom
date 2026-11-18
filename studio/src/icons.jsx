import React from "react";

// A small set of 16px line icons drawn for this interface. Every use sits next to a text
// label or carries an accessible name on its button.
function Icon({ children, size = 16 }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export const Caret = ({ open }) => (
  <Icon size={12}>{open ? <path d="M3.5 6l4.5 4.5L12.5 6" /> : <path d="M6 3.5L10.5 8 6 12.5" />}</Icon>
);
export const FileIcon = () => (
  <Icon size={14}>
    <path d="M3.5 1.5h6l3 3v10h-9z" />
    <path d="M9.5 1.5v3h3" />
  </Icon>
);
export const FolderIcon = () => (
  <Icon size={14}>
    <path d="M1.5 3.5h5l1.5 2h6.5v8h-13z" />
  </Icon>
);
export const Plus = () => (
  <Icon size={14}>
    <path d="M8 3v10M3 8h10" />
  </Icon>
);
export const Pencil = () => (
  <Icon size={14}>
    <path d="M2.5 13.5l.6-3L10.6 3l2.4 2.4-7.5 7.5z" />
  </Icon>
);
export const Trash = () => (
  <Icon size={14}>
    <path d="M2.5 4.5h11M6 4.5v-2h4v2M4 4.5l.6 9h6.8l.6-9" />
  </Icon>
);
export const Reload = () => (
  <Icon size={14}>
    <path d="M13 8a5 5 0 1 1-1.6-3.7" />
    <path d="M13 2v3h-3" />
  </Icon>
);
export const Cross = ({ size = 14 }) => (
  <Icon size={size}>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
  </Icon>
);
export const Check = () => (
  <Icon size={12}>
    <path d="M3 8.5l3.2 3L13 4.5" />
  </Icon>
);

// The product mark: three warp threads and one weft passing under, over, under. The same
// drawing, with a frame, is the application icon.
export const Mark = () => (
  <svg className="mark" width="18" height="18" viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
    <path d="M4 32h56" stroke="var(--accent)" strokeWidth="10" />
    <path d="M14 4v56M32 4v56M50 4v56" stroke="currentColor" strokeWidth="7" />
    <path d="M24 32h16" stroke="var(--accent)" strokeWidth="10" />
  </svg>
);
