# Translating SenorX99

Only English is supplied with v0.91. The app does not translate text automatically or contact a translation service.

To add a language later:

1. Copy `en.json` to a new file such as `pt.json` in this folder.
2. Keep `schema` equal to `1` and set `name` to the language's name, for example `Português`.
3. Translate the values in `strings`. Keep all identifiers unchanged: `common.continue` remains `common.continue`, while its value may become `Continuar`.
4. Preserve named placeholders exactly. For example, `{cpu}`, `{path}` and `{version}` must remain unchanged. They may move within the translated sentence.
5. Save the file as UTF-8. It will appear in the Language selector the next time the app starts. No executable rebuild or translation compilation is required.

Missing entries fall back to English. A catalog with mismatched placeholders is rejected without changing the current language. Technical BIOS setting names, CPU names, file paths, tool filenames and raw FPT diagnostics retain their technical identities.

The selected language is remembered. Switching language updates the interface without resetting the firmware workflow. Test longer translations for wrapping and clipping in the sidebar, buttons, help panel and progress summaries.

The bundled English file is checked as part of the app's required resources. Additional language files can be added without changing that resource inventory. Editing the English source catalog for a new build requires updating its resource hash as part of packaging.
