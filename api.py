#!/usr/bin/env python3

import datetime

from flask import Flask
from flask_restful import Api, Resource

from modules.crawler import UAExchangeRatesCrawler
from modules.db import Event
from version import __version__


def get_date(date_as_string):
    year = int(date_as_string[:4])
    month = int(date_as_string[4:6])
    day = int(date_as_string[6:8])

    if len(date_as_string) > 8:

        hour = int(date_as_string[8:10])
        minute = int(date_as_string[10:12])
        second = int(date_as_string[12:])

    else:

        hour = 0
        minute = 0
        second = 0

    return datetime.datetime(year, month, day, hour, minute, second)


def get_date_as_string(date: datetime.datetime) -> str:
    return date.strftime("%Y-%m-%dT%H:%M:%S")


class CrawlerHTTPService(UAExchangeRatesCrawler):
    def __init__(self, file):
        super().__init__(file, updating_event=Event.NONE)

    def get_error_response_using_date(self, date):
        return self.get_error_response(
            code=3, message=f"Unable to parse a date: {date}"
        )

    @staticmethod
    def _get_event_ttl(event: dict, event_lifespan: int) -> int:
        return round(
            event_lifespan
            - (datetime.datetime.now() - event["event_date"]).total_seconds()
        )

    def _fill_current_rates_loading_heartbeat(self, heartbeat: dict):

        event_lifespan = self._config.get(
            "heartbeat_current_rates_loading_event_lifespan"
        )
        event = self._db.get_last_event(Event.CURRENT_RATES_LOADING)

        if event is not None:

            event_ttl = self._get_event_ttl(event, event_lifespan)
            event_date = get_date_as_string(event["event_date"])

            if event_ttl <= 0:
                heartbeat["warnings"].append(
                    f"The last current rates loading triggered over {event_lifespan} seconds ago. It looks like the "
                    f"regular execution of load_current.py doesn't work."
                )

        else:

            event_date = None

            heartbeat["warnings"].append(
                "Current rates loading has never been triggered. Perhaps this is not a problem (for instance, "
                "if the application has just been deployed so load_current.py hasn't executed once yet)."
            )

        heartbeat["current_rates_loading_date"] = event_date

    def _fill_current_rates_availability_heartbeat(self, heartbeat: dict):

        availability_dates = {}
        available_currencies = []
        unavailable_currencies = []

        event_lifespan = self._config.get(
            "heartbeat_current_rates_availability_event_lifespan"
        )

        currency_codes = self.get_currency_codes()

        for currency_code in currency_codes:

            event_ttl = 0
            event_date = None

            event = self._db.get_last_event(Event.CURRENT_RATES_AVAILABILITY)

            if event is not None:
                event_ttl = self._get_event_ttl(event, event_lifespan)
                event_date = get_date_as_string(event["event_date"])

            availability_dates[currency_code] = event_date

            if event_ttl > 0:
                available_currencies.append(currency_code)
            else:
                unavailable_currencies.append(currency_code)

        if unavailable_currencies:
            heartbeat["warnings"].append(
                f"At least one currency is not available at bank's website within {event_lifespan} last seconds."
            )

        heartbeat["currencies_availability"] = {
            "availability_dates": availability_dates,
            "available_currencies": available_currencies,
            "unavailable_currencies": unavailable_currencies,
        }

    def _fill_historical_rates_loading_heartbeat(self, heartbeat: dict):

        event_lifespan = self._config.get(
            "heartbeat_historical_rates_loading_event_lifespan"
        )
        event = self._db.get_last_event(Event.HISTORICAL_RATES_LOADING)

        if event is not None:

            event_ttl = self._get_event_ttl(event, event_lifespan)
            event_date = get_date_as_string(event["event_date"])

            if event_ttl < 0:
                heartbeat["warnings"].append(
                    f"The last successful historical rates loading triggered over {event_lifespan} seconds ago."
                )

        else:

            event_date = None

            heartbeat["warnings"].append(
                "Historical rates loading has never been triggered. Perhaps this is not a problem (for instance, "
                "if the application has just been deployed so load_history.py hasn't executed once yet)."
            )

        heartbeat["historical_rates_loading_date"] = event_date

    def get_heartbeat(self) -> tuple:

        heartbeat = {
            "warnings": [],
            "current_date": get_date_as_string(datetime.datetime.now())
        }

        self._fill_current_rates_loading_heartbeat(heartbeat)
        self._fill_historical_rates_loading_heartbeat(heartbeat)

        self._fill_current_rates_availability_heartbeat(heartbeat)
        self._fill_current_rates_updating_heartbeat(heartbeat)

        return heartbeat, len(heartbeat["warnings"]) == 0

    def _fill_current_rates_updating_heartbeat(self, heartbeat: dict):

        updating_dates = {}
        updated_currencies = []
        outdated_currencies = []

        event_lifespan = self._config.get(
            "heartbeat_current_rates_updating_event_lifespan"
        )

        currency_codes = self.get_currency_codes()

        for currency_code in currency_codes:

            event_ttl = 0
            event_date = None

            event = self._db.get_last_event(Event.CURRENT_RATES_UPDATING)

            if event is not None:
                event_ttl = self._get_event_ttl(event, event_lifespan)
                event_date = get_date_as_string(event["event_date"])

            updating_dates[currency_code] = event_date

            if event_ttl > 0:
                updated_currencies.append(currency_code)
            else:
                outdated_currencies.append(currency_code)

        if outdated_currencies:
            heartbeat["warnings"].append(
                f"At least one currency is not available at bank's website within {event_lifespan} last seconds."
            )

        heartbeat["currencies_updating"] = {
            "updating_dates": updating_dates,
            "updated_currencies": updated_currencies,
            "outdated_currencies": outdated_currencies,
        }

    @staticmethod
    def get_error_response(code, message):
        data = {"error_message": message, "error_code": code}

        return data, 200

    def get_currency_codes(self) -> list:
        """
        Returns the list of currency codes set in the configuration file.
        """

        currency_codes_filter = self._config["currency_codes_filter"]
        currency_codes = self._config["currency_codes"].values()

        return (
            currency_codes_filter
            if currency_codes_filter
            else list(set(list(currency_codes)))
        )

    def get_currency_rates(
        self,
        currency_code: str,
        import_date: datetime.datetime = None,
        start_date: datetime.datetime = None,
        end_date: datetime.datetime = None,
    ):

        currency_code = currency_code.upper()

        if currency_code not in self.get_currency_codes():

            message = (
                f"Exchange rates for the currency code"
                f' "{currency_code}" cannot be found at UAE CB.'
            )

            return self.get_error_response(code=4, message=message)

        else:

            datetime_format_string = "%Y%m%d%H%M%S"
            date_format_string = "%Y%m%d"

            import_dates = []

            rates = self._db.get_currency_rates(
                currency_code, import_date, start_date, end_date
            )

            for rate in rates:
                import_dates.append(rate["import_date"])

                rate.update(
                    {
                        "import_date": rate["import_date"].strftime(
                            datetime_format_string
                        ),
                        "rate_date": rate["rate_date"].strftime(date_format_string),
                    }
                )

            max_import_date = (
                max(import_dates)
                if len(import_dates) > 0
                else datetime.datetime(1, 1, 1)
            )
            max_import_date = max_import_date.strftime(datetime_format_string)

            data = {"rates": rates, "max_import_date": max_import_date}

            return data, 200


class Hello(Resource):
    @staticmethod
    def get():
        return crawler.get_error_response(code=1, message="No action specified.")


class Info(Resource):
    @staticmethod
    def get():
        return {"version": __version__}, 200


class Currencies(Resource):
    @staticmethod
    def get():
        data = {"currencies": crawler.get_currency_codes()}

        return data, 200


class Rates(Resource):
    @staticmethod
    def get():
        return crawler.get_error_response(code=2, message="No currency specified.")


class RatesUsingCurrencyCode(Resource):
    @staticmethod
    def get(currency_code: str):
        return crawler.get_currency_rates(currency_code)


class RatesUsingCurrencyCodeAndImportDate(Resource):
    @staticmethod
    def get(currency_code: str, import_date: str):

        try:
            import_date = get_date(import_date)
        except ValueError:
            return crawler.get_error_response_using_date(import_date)

        return crawler.get_currency_rates(currency_code, import_date)


class RatesUsingCurrencyCodeAndImportDateAndStartDate(Resource):
    @staticmethod
    def get(currency_code: str, import_date: str, start_date: str):

        try:
            import_date = get_date(import_date)
        except ValueError:
            return crawler.get_error_response_using_date(import_date)

        try:
            start_date = get_date(start_date)
        except ValueError:
            return crawler.get_error_response_using_date(start_date)

        return crawler.get_currency_rates(currency_code, import_date, start_date)


class RatesUsingCurrencyCodeAndImportDateAndStartDateAndEndDate(Resource):
    @staticmethod
    def get(currency_code: str, import_date: str, start_date: str, end_date: str):

        try:
            import_date = get_date(import_date)
        except ValueError:
            return crawler.get_error_response_using_date(import_date)

        try:
            start_date = get_date(start_date)
        except ValueError:
            return crawler.get_error_response_using_date(start_date)

        try:
            end_date = get_date(end_date)
        except ValueError:
            return crawler.get_error_response_using_date(end_date)

        return crawler.get_currency_rates(
            currency_code, import_date, start_date, end_date
        )


class Heartbeat(Resource):
    @staticmethod
    def get():

        details, success = crawler.get_heartbeat()

        return details, 200 if success else 500


crawler = CrawlerHTTPService(__file__)

app = Flask(__name__)
api = Api(app)

api.add_resource(Hello, "/")

api.add_resource(Info, "/info/")

api.add_resource(Currencies, "/currencies/")

api.add_resource(Rates, "/rates/")

api.add_resource(RatesUsingCurrencyCode, "/rates/<currency_code>/")

api.add_resource(
    RatesUsingCurrencyCodeAndImportDate, "/rates/<currency_code>/<import_date>/"
)

api.add_resource(
    RatesUsingCurrencyCodeAndImportDateAndStartDate,
    "/rates/<currency_code>/<import_date>/<start_date>/",
)

api.add_resource(
    RatesUsingCurrencyCodeAndImportDateAndStartDateAndEndDate,
    "/rates/<currency_code>/<import_date>/<start_date>/<end_date>/",
)

api.add_resource(Heartbeat, "/heartbeat/")

if __name__ == "__main__":
    app.run()
