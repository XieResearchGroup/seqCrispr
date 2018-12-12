from keras.models import Sequential, Model
from keras.layers import Embedding, LSTM, Flatten, Dense, BatchNormalization, \
    Activation, Dropout, concatenate, Lambda, Reshape, Conv2D, MaxPooling2D, TimeDistributed
from keras.constraints import maxnorm
from keras.optimizers import rmsprop, TFOptimizer, Adam
import tensorflow as tf
from keras.utils.generic_utils import get_custom_objects
import utils
import sys
import importlib

config_path = ".".join(sys.argv[1].split("/")[-3:]) + "." if len(sys.argv) >= 2 and sys.argv[1].split("/")[-1].startswith("run") else ""
config = importlib.import_module(config_path+"config")


def swish(x):
    return (tf.sigmoid(x) * x)


class Swish(Activation):

    def __init__(self, activation, **kwargs):
        super(Swish, self).__init__(activation, **kwargs)
        self.__name__ = 'swish'


get_custom_objects().update({'swish': Swish(swish)})

class CrisprCasModel():

    def __init__(self, for_seq_input, bio_features, weight_matrix = None):
        self.weight_matrix = weight_matrix
        self.seq_input_len = int(for_seq_input.shape[1])
        self.bio_features_len = int(bio_features.shape[1])
        self.seq_input = for_seq_input
        self.bio_features = bio_features
        self.for_seq_input_index = range(self.seq_input_len)
        self.rev_seq_input_index = range(len(self.for_seq_input_index), len(self.for_seq_input_index))
        self.bio_features_index = range(len(self.for_seq_input_index)+len(self.rev_seq_input_index),
                                        len(self.for_seq_input_index)+len(self.rev_seq_input_index)+self.bio_features_len)
        self.off_target_features_index = range(len(self.for_seq_input_index)+len(self.rev_seq_input_index)+len(self.bio_features_index),
                                               len(self.for_seq_input_index) + len(self.rev_seq_input_index) + len(
                                                   self.bio_features_index))


    def __seq_embedding_cnn(self, input, name_suffix = '', nt = 3):

        # Creat embedding and sequential cnn layers
        weights = self.weight_matrix
        voca_size = config.embedding_voca_size
        vec_dim = config.embedding_vec_dim
        input_len = self.seq_input_len

        if nt == 1:
            weights = None
            voca_size = 5
            vec_dim = 8
            input_len = 20

        def cov_model(kernel_size = (3,1), pool_size = (21-3+1,1), levels = config.cnn_levels):

            model = Sequential()
            model.add(Conv2D(levels[0], input_shape=(input_len, vec_dim, 1), kernel_size=(kernel_size[0], 1),
                              padding='same'))
            model.add(BatchNormalization())
            model.add(Activation('relu'))
            # output shape is (None, len, dim, 32)
            model.add(MaxPooling2D(pool_size=(2,1), padding='same'))
            # output shape is (None, len+1/2, dim, 32)

            for i in range(len(levels)-2):
                model.add(Conv2D(levels[i+1], kernel_size=(kernel_size[0], 1), padding='same'))
                # output shape is (None, len+1/2, dim, 64)
                model.add(BatchNormalization())
                model.add(Activation('relu'))
                model.add(MaxPooling2D(pool_size=(2, 1), padding='same'))
                # output shape is (None, (len+1/2+1)/2, dim, 64)

            last_kernal_size = (3, kernel_size[1])
            model.add(Conv2D(config.cnn_levels[-1], kernel_size=last_kernal_size, strides= (1, kernel_size[1]), padding='same'))
            model.add(Activation('relu'))
            # output shape is (None, (len+1/2+1)/2-ker_len+1, 1, 128)
            last_pool_len = pool_size[0]
            for _ in range(len(levels)-1):
                last_pool_len =(last_pool_len + 1) / 2
            last_pool_size = (last_pool_len, 1)
            model.add(MaxPooling2D(pool_size=last_pool_size, padding='valid'))
            model.add(Flatten())
            utils.output_model_info(model)
            return model

        def embedding_model(input):

            em = Embedding(voca_size, vec_dim, weights= weights,
                            input_length=input_len, trainable=True)
            embedded_input = em(input)
            reshaped_embedded_input = (Reshape((input_len, vec_dim, 1)))(embedded_input)
            return reshaped_embedded_input

        reshaped_embedded_input_1 = embedding_model(input = input)
        cov_1_1 = cov_model(kernel_size=(3,vec_dim), pool_size=(input_len, 1))(reshaped_embedded_input_1)
        cov_1_2 = cov_model(kernel_size=(5, vec_dim), pool_size=(input_len, 1))(reshaped_embedded_input_1)

        cnn_total = concatenate([cov_1_1, cov_1_2])
        return cnn_total


    def __embedding_cnn(self, name_suffix = '', nt = 3):

        # Creat embedding and cnn layers
        weights = self.weight_matrix
        voca_size = config.embedding_voca_size
        vec_dim = config.embedding_vec_dim
        input_len = self.seq_input_len

        if nt == 1:
            weights = None
            voca_size = 5
            vec_dim = 8
            input_len = 20

        model = Sequential(name='embedding_and_cnn_' + name_suffix)
        model.add(Embedding(voca_size, vec_dim, weights= weights, input_length=input_len, trainable=True))
        model.add(Reshape((1, input_len, vec_dim)))
        model.add(Conv2D(32, kernel_size=(1, 4), strides=2, padding='same'))
        model.add(BatchNormalization(momentum=0))
        model.add(Activation('relu'))
        # (1, 10, 32)
        model.add(Conv2D(64, kernel_size=(1, 4), strides=2, padding='same'))
        model.add(BatchNormalization(momentum=0))
        model.add(Activation('relu'))
        # (1, 5, 64)
        model.add(Conv2D(128, kernel_size=(1, 4), strides=2, padding='same'))
        model.add(BatchNormalization(momentum=0))
        model.add(Activation('relu'))
        # (1,3,128)
        model.add(Conv2D(256, kernel_size=(1, 3), strides=2, padding='valid'))
        model.add(BatchNormalization(momentum=0))
        model.add(Activation('relu'))
        model.add(Flatten())
        model.add(Dense(units=config.cnn_levels[-1]))
        utils.output_model_info(model)
        return model

    def __embedding_rnn(self, name_suffix = ''):

        # Creat embedding and rnn layers
        model = Sequential(name='embedding_and_rnn_' + name_suffix)
        # activation function is tanh, gates using sigmoid function
        model.add(Embedding(config.embedding_voca_size, config.embedding_vec_dim, weights= self.weight_matrix, input_length=self.seq_input_len, trainable=True))
        model.add(Dropout(rate=config.dropout))
        # embedding layer output shape is (batch_size,  self.seq_input_len=21, config.embedding_vec_dim=32)
        for _ in range(config.LSTM_stacks_num):
            model.add(LSTM(config.LSTM_hidden_unit, return_sequences=True, dropout=config.dropout, kernel_constraint=maxnorm(config.maxnorm)))

        # output shape is (batch_size,  self.seq_input_len=21, config.LSTM_hidden_unit=8)
        model.add(TimeDistributed(Dense(config.rnn_time_distributed_dim)))
        model.add(Flatten())
        return model

    def __fully_connected(self, nodes_unit_nums, input_len, name_suffix= ''):

        model = Sequential(name = 'FC_' + name_suffix)

        for i in xrange(len(nodes_unit_nums)):

            if i == 0:
                model.add(Dense(nodes_unit_nums[i], input_shape=(input_len,), kernel_constraint=maxnorm(config.maxnorm)))
            else:
                model.add(Dense(nodes_unit_nums[i], kernel_constraint=maxnorm(config.maxnorm)))

            model.add(BatchNormalization())
            model.add(Activation(config.activation_method[i%len(config.activation_method)]))
            model.add(Dropout(rate=config.dropout))
        utils.output_model_info(model)
        return model

    def __cas9_rnn_model(self):

        # Embedding and LSTM model is in the front
        seq2vec_input = self.seq_input
        rnn_output = self.__embedding_rnn(name_suffix='for')(seq2vec_input)

        # concatenate rnn trained features and extra features
        extra_raw_input = self.bio_features
        if self.bio_features_len:
            fully_connected_bio = self.__fully_connected(config.bio_fully_connected_layer_layout, self.bio_features_len, "bio")
            processed_bio_features = fully_connected_bio(extra_raw_input)
        else:
            processed_bio_features = extra_raw_input


        merged_features = concatenate([processed_bio_features, rnn_output])
        dropouted_merged_features = Dropout(rate=0.2)(merged_features)

        # fully connected layer
        used_seq_input_len = self.seq_input_len
        fully_connected_output = self.__fully_connected(config.fully_connected_layer_layout,
                                                        config.bio_fully_connected_layer_layout[-1] + used_seq_input_len * config.rnn_time_distributed_dim)(dropouted_merged_features)
        dropouted_fully_connected_output = Dropout(rate=0.2)(fully_connected_output)
        output = Dense(1, kernel_constraint=maxnorm(config.maxnorm))(dropouted_fully_connected_output)

        # Build the model
        crispr_model = Model(inputs=[seq2vec_input, extra_raw_input], outputs=[output])
        return crispr_model

    def __cas9_mixed_model(self):

        #Embedding and LSTM model is in the front
        seq2vec_input = self.seq_input
        rnn_output = self.__embedding_rnn(name_suffix='for')(seq2vec_input)

        #Embedding and CNN model is in the front
        seq2vec_input = self.seq_input
        if config.seq_cnn:
            cnn_output = self.__seq_embedding_cnn(input = seq2vec_input, name_suffix='for')
        else:
            cnn_output = self.__embedding_cnn(name_suffix='for')(seq2vec_input)

        # concatenate rnn trained features and extra features
        extra_raw_input = self.bio_features
        if self.bio_features_len:
            fully_connected_bio = self.__fully_connected(config.bio_fully_connected_layer_layout, self.bio_features_len, "bio")
            processed_bio_features = fully_connected_bio(extra_raw_input)
        else:
            processed_bio_features = extra_raw_input

        merged_features = concatenate([processed_bio_features, cnn_output, rnn_output])
        dropouted_merged_features = Dropout(rate=0.2)(merged_features)

        # fully connected layer

        if config.seq_cnn:
            cnn_len = config.cnn_levels[-1] * 2
        else:
            cnn_len = config.cnn_levels[-1]
        used_seq_input_len = self.seq_input_len # self.seq_input_len
        input_len = config.bio_fully_connected_layer_layout[-1] + used_seq_input_len * config.rnn_time_distributed_dim + cnn_len
        fully_connected_output = self.__fully_connected(config.fully_connected_layer_layout, input_len)(dropouted_merged_features)
        dropouted_fully_connected_output = Dropout(rate=0.2)(fully_connected_output)
        output = Dense(1, kernel_constraint=maxnorm(config.maxnorm))(dropouted_fully_connected_output)

        # Build the model
        crispr_model = Model(inputs=[seq2vec_input, extra_raw_input], outputs=[output])
        return crispr_model

    def __cas9_cnn_model(self):

        seq2vec_input = self.seq_input
        if config.seq_cnn:
            cnn_output = self.__seq_embedding_cnn(input = seq2vec_input, name_suffix='for')
        else:
            cnn_output = self.__embedding_cnn(name_suffix='for')(seq2vec_input)

        # concatenate rnn trained features and extra features
        extra_raw_input = self.bio_features
        if self.bio_features_len:
            fully_connected_bio = self.__fully_connected(config.bio_fully_connected_layer_layout, self.bio_features_len, "bio")
            processed_bio_features = fully_connected_bio(extra_raw_input)
        else:
            processed_bio_features = extra_raw_input

        merged_features = concatenate([processed_bio_features, cnn_output])
        dropouted_merged_features = Dropout(rate=0.2)(merged_features)

        # fully connected layer
        # cnn_final_dim = ((config.embedding_vec_dim)/2)/2
        # cnn_final_len = ((self.seq_input_len)/2)/2
        # cnn_len = 64 * cnn_final_dim * cnn_final_len
        if config.seq_cnn:
            cnn_len = config.cnn_levels[-1] * 2
        else:
            cnn_len = config.cnn_levels[-1]
        input_len = config.bio_fully_connected_layer_layout[-1] + cnn_len
        fully_connected_output = self.__fully_connected(config.fully_connected_layer_layout, input_len)(dropouted_merged_features)
        dropouted_fully_connected_output = Dropout(rate=0.2)(fully_connected_output)
        output = Dense(1, kernel_constraint=maxnorm(config.maxnorm))(dropouted_fully_connected_output)

        # Build the model
        crispr_model = Model(inputs=[seq2vec_input, extra_raw_input], outputs=[output])
        return crispr_model


    def get_raw_model(self, method = config.model_type):

        crispr_model = getattr(self, "_{!s}__cas9_{!s}_model".format(self.__class__.__name__, method),
                               self.__cas9_rnn_model)()
        return crispr_model

    def get_model(self, method = config.model_type):

        crispr_model = getattr(self, "_{!s}__cas9_{!s}_model".format(self.__class__.__name__, method), self.__cas9_rnn_model)()
        return self.compile_transfer_learning_model(crispr_model)

    @classmethod
    def compile_transfer_learning_model(cls, model):

        custimized_rmsprop = Adam(lr=config.start_lr, decay=config.lr_decay)
        model.compile(optimizer=custimized_rmsprop, loss='mse', metrics=[utils.revised_mse_loss, 'mse'])
        return model


    def get_tf_model(self, ground_truth, method = config.model_type):

        global_step = tf.Variable(0, trainable=False)

        learn_rate = tf.train.cosine_decay_restarts(learning_rate=0.001, global_step=global_step, first_decay_steps=100)
        crispr_model = getattr(self, "_{!s}__cas9_{!s}_model".format(self.__class__.__name__, method),
                               self.__cas9_rnn_model)()

        loss = tf.losses.mean_squared_error(ground_truth, crispr_model.output)
        rmsprop_optimizer = tf.train.RMSPropOptimizer(learning_rate=learn_rate).minimize(loss, global_step=global_step)

        custimized_rmsprop = TFOptimizer(rmsprop_optimizer)
        crispr_model.compile(optimizer=custimized_rmsprop, loss='mse', metrics=[utils.revised_mse_loss, 'mse'])
        return crispr_model

