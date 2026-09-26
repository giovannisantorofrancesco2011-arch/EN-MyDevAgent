// Site interactivity: light/dark theme and a greeting counter.

document.getElementById("year").textContent = new Date().getFullYear();

const theme = document.getElementById("theme");
theme.addEventListener("click", () => {
  const dark = document.body.classList.toggle("dark");
  theme.textContent = dark ? "☀️" : "🌙";
});

let greetings = 0;
document.getElementById("wave").addEventListener("click", () => {
  greetings += 1;
  document.getElementById("greetings").textContent =
    greetings === 1 ? "You said hi 1 time 👋" : `You said hi ${greetings} times 👋`;
});
