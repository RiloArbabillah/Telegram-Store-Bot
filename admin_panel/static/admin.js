document.addEventListener("click", function (event) {
  const target = event.target instanceof Element ? event.target : null;
  const openButton = target ? target.closest("[data-dialog-open]") : null;
  if (openButton) {
    const dialog = document.getElementById(openButton.dataset.dialogOpen);
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    return;
  }

  const closeButton = target ? target.closest("[data-dialog-close]") : null;
  if (closeButton) {
    const dialog = closeButton.closest("dialog");
    if (dialog) dialog.close();
    return;
  }

  if (event.target instanceof HTMLDialogElement) {
    event.target.close();
  }
});

document.addEventListener("submit", function (event) {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});
