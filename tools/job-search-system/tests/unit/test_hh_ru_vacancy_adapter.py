from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from job_search.infrastructure.board_adapters.hh_ru_vacancy_adapter import HhRuVacancyAdapter  # noqa: E402


class HhRuVacancyAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = HhRuVacancyAdapter()

    def test_extracts_hh_ru_single_vacancy_page_with_url_header(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "https://hh.ru/vacancy/133290828?hhtmFrom=vacancy_search_list",
                    "",
                    "Руководитель IT-департамента (CIO)",
                    "Уровень дохода не указан",
                    "Опыт работы: 3-6 лет",
                    "",
                    "Полная занятость",
                    "Оформление: Трудовой договор",
                    "График: 5/2",
                    "Формат работы: на месте работодателя",
                    "",
                    "Сейчас эту вакансию смотрят 12 человек",
                    "",
                    "Wallet One",
                    "Wallet One",
                    "4,4",
                    "19 отзывов",
                    "Москва, Пресненская набережная",
                    "",
                    "WALLET ONE - технологичный лидер на рынке платежных решений.",
                    "Ключевые задачи:",
                    "Стратегия и архитектура: Разработка и внедрение стратегии развития IT-инфраструктуры.",
                    "Где предстоит работать",
                    "Москва, Новохохловская улица, 89с3",
                    "Показать на большой карте",
                    "Вакансия опубликована 1 июня 2026 в Москве",
                ]
            ),
            source_origin="vacancy_page",
        )

        self.assertEqual(extraction.warnings, [])
        self.assertEqual(len(extraction.items), 1)
        item = extraction.items[0]
        self.assertEqual(item.title, "Руководитель IT-департамента (CIO)")
        self.assertEqual(item.company_name, "Wallet One")
        self.assertEqual(item.location_text, "Москва, Новохохловская улица, 89с3 · On-site")
        self.assertEqual(item.source_url, "https://hh.ru/vacancy/133290828")
        self.assertEqual(item.external_vacancy_id, "133290828")
        self.assertEqual(item.source_published_at, "2026-06-01")
        self.assertIsNone(item.source_updated_at)
        self.assertIn("hh_salary_text=Уровень дохода не указан", item.raw_text or "")
        self.assertIn("hh_experience_text=Опыт работы: 3-6 лет", item.raw_text or "")
        self.assertIn("hh_work_model=On-site", item.raw_text or "")
        self.assertIn("hh_published_at=2026-06-01", item.raw_text or "")

    def test_extracts_hh_ru_search_result_cards(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "Сейчас смотрят 24 человека",
                    "",
                    "[Product manager](https://hh.ru/vacancy/133720023?hhtmFrom=vacancy_search_list)",
                    "",
                    "от 400 000 ₽ за месяц, на руки",
                    "",
                    "Опыт более 6 лет",
                    "",
                    "Выплаты: два раза в месяц",
                    "",
                    "[ООО ЭЛМ Технологии](https://hh.ru/employer/6141685?hhtmFrom=vacancy_search_list)",
                    "",
                    "Москва",
                    "",
                    "Кутузовская и еще 2",
                    "",
                    "Отклик без резюме",
                    "",
                    "Формирование продуктовой стратегии и roadmap развития продукта...",
                    "",
                    "[Директор по разработке](https://hh.ru/vacancy/133680195?hhtmFrom=vacancy_search_list)",
                    "",
                    "до 250 000 ₽ за месяц, на руки",
                    "",
                    "Опыт 3-6 лет",
                    "",
                    "Можно удалённо",
                    "",
                    "[ООО Пруфикс](https://hh.ru/employer/2192358?hhtmFrom=vacancy_search_list)",
                    "",
                    "Москва",
                    "",
                    "Зорге и еще 3",
                    "",
                    "Взять на себя операционное управление всей разработкой...",
                ]
            ),
            source_origin="search_results",
        )

        self.assertEqual(extraction.warnings, [])
        self.assertEqual(len(extraction.items), 2)
        first, second = extraction.items
        self.assertEqual(first.title, "Product manager")
        self.assertEqual(first.company_name, "ООО ЭЛМ Технологии")
        self.assertEqual(first.location_text, "Москва · Кутузовская и еще 2")
        self.assertEqual(first.source_url, "https://hh.ru/vacancy/133720023")
        self.assertEqual(first.external_vacancy_id, "133720023")
        self.assertIn("hh_salary_text=от 400 000 ₽ за месяц, на руки", first.raw_text or "")
        self.assertIn("hh_experience_text=Опыт более 6 лет", first.raw_text or "")
        self.assertEqual(second.title, "Директор по разработке")
        self.assertEqual(second.company_name, "ООО Пруфикс")
        self.assertEqual(second.location_text, "Москва · Зорге и еще 3 · Remote")
        self.assertIn("hh_work_model=Remote", second.raw_text or "")

    def test_ignores_hh_company_recommendations_without_vacancy_links(self) -> None:
        extraction = self.adapter.extract_from_text(
            "\n".join(
                [
                    "## Компании для вас",
                    "![КОМИТАС](https://img.hhcdn.ru/employer-logo-round/1705011.jpeg)",
                    "КОМИТАС",
                    "18 активных вакансий",
                ]
            ),
            source_origin="search_results",
        )

        self.assertEqual(extraction.items, [])
        self.assertEqual(extraction.warnings, ["no_hh_ru_vacancy_links_found"])


if __name__ == "__main__":
    unittest.main()
