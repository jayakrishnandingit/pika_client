# -*- coding: utf-8 -*-

import logging
import json
import pika

from mill_common.mixins import CallbackMixin
from mill_common.base import Connector

LOGGER = logging.getLogger(__name__)


class BasePublisher(CallbackMixin, object):
    def __init__(
            self,
            amqp_url,
            app_id='',
            exchange='',
            exchange_type='topic',
            queue=None,
            routing_key='',
            reconnect_interval=None,
            **kwargs):
        super().__init__()
        self.amqp_url = amqp_url
        self.connector = self.get_connector_class()(
            amqp_url=self.amqp_url,
            app_id=app_id,
            exchange=exchange,
            exchange_type=exchange_type,
            queue=queue,
            routing_key=routing_key,
            reconnect_interval=reconnect_interval)
        self.connector.register_callback('on_bindok', self.start)

    def get_connector_class(self):
        return Connector

    def start(self):
        """
        Method to start whatever the interface is designated to do.
        """
        self.enable_delivery_confirmations()
        self._process_callbacks('start')

    def enable_delivery_confirmations(self):
        """Send the Confirm.Select RPC method to RabbitMQ to enable delivery
        confirmations on the channel. The only way to turn this off is to close
        the channel and create a new one.

        When the message is confirmed from RabbitMQ, the
        on_delivery_confirmation method will be invoked passing in a Basic.Ack
        or Basic.Nack method from RabbitMQ that will indicate which messages it
        is confirming or rejecting.

        """
        LOGGER.info('Issuing Confirm.Select RPC command')
        self.connector.channel.confirm_delivery(self.on_delivery_confirmation)

    def on_delivery_confirmation(self, method_frame):
        """Invoked by pika when RabbitMQ responds to a Basic.Publish RPC
        command, passing in either a Basic.Ack or Basic.Nack frame with
        the delivery tag of the message that was published. The delivery tag
        is an integer counter indicating the message number that was sent
        on the channel via Basic.Publish. Here we're just doing house keeping
        to keep track of stats and remove message numbers that we expect
        a delivery confirmation of from the list used to keep track of messages
        that are pending confirmation.

        :param pika.frame.Method method_frame: Basic.Ack or Basic.Nack frame

        """
        confirmation_type = method_frame.method.NAME.split('.')[1].lower()
        LOGGER.info('Received %s for delivery tag: %i',
                    confirmation_type,
                    method_frame.method.delivery_tag)
        if confirmation_type == 'ack':
            LOGGER.info('Acknowledged message %i.', method_frame.method.delivery_tag)
        elif confirmation_type == 'nack':
            # TODO: retry logic.
            raise NotImplementedError

    def publish_message(self, message):
        """If the class is not stopping, publish a message to RabbitMQ,
        appending a list of deliveries with the message number that was sent.
        This list will be used to check for delivery confirmations in the
        on_delivery_confirmations method.

        Once the message has been sent, schedule another message to be sent.
        The main reason I put scheduling in was just so you can get a good idea
        of how the process is flowing by slowing down and speeding up the
        delivery intervals by changing the PUBLISH_INTERVAL constant in the
        class.

        """
        channel = self.connector.channel
        if channel is None or not channel.is_open:
            LOGGER.error("Cannot publish the message. Channel unavailable or closed.")
            return

        headers = {}
        properties = pika.BasicProperties(
            app_id=self.connector.APP_ID,
            content_type='application/json',
            headers=headers)

        channel.basic_publish(
            self.connector.EXCHANGE,
            self.connector.ROUTING_KEY,
            json.dumps(message, ensure_ascii=False),
            properties)

        LOGGER.info('Published message # %s', message)

    def stop(self):
        """Stop the service by closing the channel and connection. We
        set a flag here so that we stop scheduling new messages to be
        published. The IOLoop is started because this method is
        invoked by the Try/Catch below when KeyboardInterrupt is caught.
        Starting the IOLoop again will allow the publisher to cleanly
        disconnect from RabbitMQ.
        """
        LOGGER.info('Stopping.')
        self.connector._closing = True
        self.connector.close_channel()
        self.connector.close_connection()
        LOGGER.info("Stopped.")

    def run(self):
        """
        Connect and start ioloop.
        """
        self.connector.run()