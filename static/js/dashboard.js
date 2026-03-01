document.addEventListener("DOMContentLoaded", () => {
    console.log("Dashboard loaded");

    const cards = document.querySelectorAll(".dashboard-card");

    cards.forEach(card => {
        card.addEventListener("mouseenter", () => {
            card.classList.add("hovered");
        });

        card.addEventListener("mouseleave", () => {
            card.classList.remove("hovered");
        });
    });
});