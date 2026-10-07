// Small dialog that collects optional instructions for the agent. It closes
// with the entered text, or with undefined when the user cancels.

import * as SDK from "azure-devops-extension-sdk";

interface DialogConfiguration {
  dialog?: { close: (result?: string) => void };
}

void SDK.init().then(async () => {
  await SDK.ready();
  const configuration = SDK.getConfiguration() as DialogConfiguration;
  const notes = document.getElementById("notes") as HTMLTextAreaElement;
  const close = (result?: string) => configuration.dialog?.close(result);

  document.getElementById("queue")?.addEventListener("click", () => close(notes.value));
  document.getElementById("cancel")?.addEventListener("click", () => close(undefined));
  notes.focus();
  SDK.resize(undefined, document.body.scrollHeight);
});
