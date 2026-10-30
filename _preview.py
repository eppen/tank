import os

os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame
import tank_game as g

world = pygame.Surface((g.SCREEN_WIDTH, g.SCREEN_HEIGHT))
tanks = pygame.sprite.Group()
bullets = pygame.sprite.Group()
exps = pygame.sprite.Group()

t1 = g.Tank(300, 420, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                              "left": pygame.K_a, "right": pygame.K_d,
                              "fire": pygame.K_SPACE})
t2 = g.Tank(520, 200, g.BLUE, {"forward": pygame.K_UP, "back": pygame.K_DOWN,
                               "left": pygame.K_LEFT, "right": pygame.K_RIGHT,
                               "fire": pygame.K_RETURN})
t1.angle = 45
t2.angle = 200
t2.hp = 60
t2.flash = 6
t1.lives = 3          # 预览里顺便展示命数 HUD（红 3 条、蓝 1 条）
t2.lives = 1
tanks.add(t1, t2)
for t in tanks:
    t.sync_image()

bullets.add(g.Bullet(420, 330, 45, t1), g.Bullet(600, 330, 270, t2))
exps.add(g.Explosion(430, 260, g.YELLOW, 30, 14))
for _ in range(6):
    exps.update()

g.draw_background(world)
tanks.draw(world)
bullets.draw(world)
exps.draw(world)
for t in tanks:
    g.draw_health_bar(world, t)

screen = g.screen
screen.blit(world, (0, 0))
font = pygame.font.SysFont(None, 22)
small = pygame.font.SysFont(None, 18)
font2 = pygame.font.SysFont(None, 56)
h1 = font.render("P1 (red): W/S move, A/D turn, SPACE fire", True, (200, 120, 120))
h2 = font.render("P2 (blue): arrows, ENTER fire", True, (120, 150, 220))
screen.blit(h1, (12, 10))
screen.blit(h2, (g.SCREEN_WIDTH - h2.get_width() - 12, 10))
# 直接用游戏内同一份 HUD 文案（HP / ANG / ERR / DIST），不再复制一份格式串
t1_text = small.render(g.hud_text(t1, t2, 100), True, (200, 120, 120))
t2_text = small.render(g.hud_text(t2, t1, 60), True, (120, 150, 220))
screen.blit(t1_text, (12, 34))
screen.blit(t2_text, (g.SCREEN_WIDTH - t2_text.get_width() - 12, 34))
g.draw_lives(screen, t1, 12, 54)
g.draw_lives(screen, t2, g.SCREEN_WIDTH - 12 - g.lives_pips_width(), 54)
win = font2.render("P1 (red) wins!", True, (255, 255, 255))
screen.blit(win, win.get_rect(center=(g.SCREEN_WIDTH // 2, g.SCREEN_HEIGHT // 2 + 120)))
pygame.image.save(screen, "tank_preview.png")
print("saved tank_preview.png", pygame.image.load("tank_preview.png").get_size())
