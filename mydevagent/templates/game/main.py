"""Catch the Stars: move the basket with the arrow keys and catch the falling stars.

Run: python main.py  (first: python -m pip install -r requirements.txt)
"""

import random

import pygame

WIDTH, HEIGHT = 640, 480
FPS = 60
PLAYER_SPEED = 7
LIVES = 3
BACKGROUND = (20, 12, 40)
PURPLE = (168, 85, 247)
YELLOW = (250, 204, 21)
WHITE = (243, 232, 255)


def new_star() -> pygame.Rect:
    return pygame.Rect(random.randint(0, WIDTH - 16), -16, 16, 16)


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Catch the Stars")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 36)

    player = pygame.Rect(WIDTH // 2 - 40, HEIGHT - 40, 80, 16)
    stars = [new_star()]
    score, lives = 0, LIVES

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_r and lives <= 0:
                stars, score, lives = [new_star()], 0, LIVES  # restart

        if lives > 0:
            keys = pygame.key.get_pressed()
            player.x += (keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]) * PLAYER_SPEED
            player.clamp_ip(screen.get_rect())

            for star in stars[:]:
                star.y += 3 + score // 5  # the more points you score, the faster they fall
                if star.colliderect(player):
                    stars.remove(star)
                    score += 1
                elif star.top > HEIGHT:
                    stars.remove(star)
                    lives -= 1
            while len(stars) < 1 + score // 10:
                stars.append(new_star())

        screen.fill(BACKGROUND)
        pygame.draw.rect(screen, PURPLE, player, border_radius=8)
        for star in stars:
            pygame.draw.circle(screen, YELLOW, star.center, star.width // 2)
        screen.blit(font.render(f"Score: {score}   Lives: {lives}", True, WHITE), (16, 12))
        if lives <= 0:
            text = font.render("Game over! Press R to restart", True, WHITE)
            screen.blit(text, text.get_rect(center=(WIDTH // 2, HEIGHT // 2)))
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
