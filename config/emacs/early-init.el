;;; early-init.el --- Early initialization -*- lexical-binding: t; -*-

(when (version< emacs-version "31.1")
  (error "This configuration requires Emacs 31.1 or newer (running %s)"
         emacs-version))

(setq package-enable-at-startup nil
      gc-cons-threshold most-positive-fixnum
      gc-cons-percentage 0.6
      frame-inhibit-implied-resize t)

;; Use English for Emacs packages and every subprocess it starts (including
;; Git).  Do not set LC_ALL=C: it overrides the inherited UTF-8 locale and
;; makes subprocesses report US-ASCII on macOS, breaking non-ASCII output and
;; file names.  LC_MESSAGES controls message language without changing the
;; character encoding selected by LANG/LC_CTYPE.
(set-language-environment "English")
(setenv "LANGUAGE" "en")
(setenv "LC_ALL" nil)
(setenv "LC_MESSAGES" "C")

;; Bundled libgccjit can infer a nonexistent macOS version from newer Darwin
;; releases (for example Darwin 27 -> macOS 18).  Set the real target before
;; package initialization: advice can compile native trampolines during init.
;; See https://github.com/jimeh/emacs-builds/issues/71.
(defun my-native-comp-setup-macos-target ()
  "Use this Mac's OS version for native compilation unless explicitly set."
  (when (and (eq system-type 'darwin)
             (member (getenv "MACOSX_DEPLOYMENT_TARGET") '(nil "")))
    (let ((version (ignore-errors
                     (car (process-lines "/usr/bin/sw_vers" "-productVersion")))))
      (when (and version
                 (string-match "\\`\\([0-9]+\\.[0-9]+\\)\\(?:\\.[0-9]+\\)?\\'" version))
        (setenv "MACOSX_DEPLOYMENT_TARGET" (match-string 1 version))))))

(my-native-comp-setup-macos-target)

;; Route keyboard input through the GTK input-method context instead of Emacs's
;; own XIM client.  With fcitx5 the XIM path never engages: keystrokes land in
;; the buffer raw and Shift-based Chinese/ASCII switching does nothing.  The GTK
;; path goes through fcitx5's im module (im-fcitx5.so) over D-Bus and works.
;; Read when a frame is created, so it has to be set here rather than in `init'.
;;
;; `x-gtk-use-native-input' is a C-level DEFVAR compiled in only under USE_GTK,
;; so it is absent from the macOS (NS) build.  The `boundp' guard keeps this a
;; no-op there, and also keeps the byte-compiler from reporting an assignment to
;; a free variable when the file is compiled or linted on macOS.
(when (boundp 'x-gtk-use-native-input)
  (setq x-gtk-use-native-input t))

;; File-name handlers are mainly for remote files and slow down startup.
(defvar init-file-name-handler-alist file-name-handler-alist)
(setq file-name-handler-alist nil)
(add-hook 'emacs-startup-hook
          (lambda ()
            ;; A package loaded during init may have registered another
            ;; handler.  Preserve it instead of replacing the live list with
            ;; the startup snapshot.
            (setq file-name-handler-alist
                  (delete-dups
                   (append file-name-handler-alist
                           init-file-name-handler-alist)))))

(menu-bar-mode (if (and (eq system-type 'darwin) (featurep 'ns)) 1 -1))

(dolist (mode '(tool-bar-mode scroll-bar-mode))
  (when (fboundp mode)
    (funcall mode -1)))

;; On macOS, fullscreen stays on the current desktop instead of creating a Space.
(when (boundp 'ns-use-native-fullscreen)
  (setq ns-use-native-fullscreen nil))

;; Start initial and client frames near the upper-left corner, with a small
;; margin, at 120 columns by 36 rows. Explicit placement avoids WM cascading.
(dolist (parameters '(initial-frame-alist default-frame-alist))
  (setf (alist-get 'width (symbol-value parameters)) 120
        (alist-get 'height (symbol-value parameters)) 36
        (alist-get 'left (symbol-value parameters)) 48
        (alist-get 'top (symbol-value parameters)) 48
        (alist-get 'user-position (symbol-value parameters)) t
        (alist-get 'fullscreen (symbol-value parameters)) nil))

(provide 'early-init)

;;; early-init.el ends here
