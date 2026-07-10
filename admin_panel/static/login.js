(function () {
  const token = window.location.hash.slice(1);
  if (!token) return;

  window.history.replaceState(null, "", window.location.pathname);
  document.getElementById("login-token").value = token;
  document.getElementById("login-status").textContent = "Memverifikasi tautan login...";
  document.getElementById("login-form").requestSubmit();
}());
